/* deskdeck bridge: TCP 7788 ↔ UART (921600, GPIO13 RX / GPIO15 TX via Serial.swap).
 *
 * Transparent, with a single exception, the first HELLO: a newly connected client must send a valid
 * HELLO within 3 s whose token matches DESKDECK_TOKEN. If it does, it replaces the previous client
 * and the HELLO is forwarded to the STM32; otherwise the connection is closed. While no authenticated
 * client is connected a STATUS frame is sent to the STM32 every 2 s (never during a session: it would
 * corrupt the frames in flight).
 * Debug log: sleep 60 | nc deskdeck.local 23 (stdin must stay open; Serial belongs to the STM32).
 * Network diagnostics: packets sent to UDP 7789 are echoed back (loss/latency measurement); the
 * exact payload "deskdeck?" instead returns the bridge status as text (uptime, reset reason, Wi-Fi
 * events). The host logs it on every connect.
 * Wi-Fi watchdog: if the connection does not come back within kWifiWatchdogMs the ESP restarts
 * (counted in RTC memory).
 * Roaming: on mesh networks the ESP8266 does not move to a closer node by itself (it can stay stuck
 * on a distant node at -85 dBm). When the signal is weak it scans and moves to a clearly stronger BSSID.
 * Changing Wi-Fi: hold the board's FLASH button for 3 s and release → credentials are erased and
 * the setup portal (DeskDeck-Setup) opens.
 * Protocol: docs/protocol.md */
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <ESP8266mDNS.h>
#include <WiFiManager.h>
#include <WiFiUdp.h>
#include <stdarg.h>

#include "secrets.h"


namespace {

constexpr uint16_t kPort = 7788;
constexpr uint16_t kLogPort = 23;
constexpr uint32_t kBaud = 921600;
constexpr uint32_t kHelloTimeoutMs = 3000;
constexpr uint32_t kStatusIntervalMs = 2000;
constexpr uint16_t kPortalTimeoutS = 300; /* then restart and retry the saved network */
constexpr char kHostname[] = "deskdeck";
constexpr char kPortalSsid[] = "DeskDeck-Setup";
constexpr uint8_t kFlashButtonPin = 0; /* NodeMCU FLASH button, GPIO0, LOW while pressed */
constexpr uint32_t kWifiResetHoldMs = 3000;
constexpr uint32_t kWifiWatchdogMs = 120000;
constexpr char kStatusQuery[] = "deskdeck?";
constexpr int32_t kRoamRssiDbm = -70;     /* below this, look for a better node */
constexpr int32_t kRoamGainDb = 10;       /* switch only if at least this much stronger */
constexpr uint32_t kRoamCheckMs = 30000;  /* the weak signal must last this long */
constexpr uint32_t kRoamRetryMs = 300000; /* if nothing better: a scan interrupts the link, keep it rare */

constexpr uint8_t kSync0 = 0xA5, kSync1 = 0x5A;
constexpr uint8_t kMsgHello = 0x01, kMsgStatus = 0x40;
constexpr size_t kHeaderLen = 5, kCrcLen = 2, kMaxPayload = 512;
/* Write timeout towards the host. With the default 5000 ms, when lwIP cannot queue a packet write()
 * blocks for seconds waiting for an ACK, and with the loop stalled the host → STM32 direction stops too. */
constexpr uint32_t kTcpWriteTimeoutMs = 10;

enum Status : uint8_t { kWifiConnecting = 1, kPortal = 2, kWaitingMac = 3 };

WiFiServer server(kPort);
WiFiServer log_server(kLogPort);
WiFiUDP udp_echo; /* diagnostics: echoes UDP packets sent to 7789 */
/* WiFiClient's bool conversion cannot be trusted: stop() does not release the context, and with
 * unread data left available() > 0 keeps it looking "connected" (this caused a flood of STATUS frames).
 * State is tracked with explicit flags and a closed client is replaced by an empty object. */
WiFiClient client;  /* the single authenticated client */
bool client_active;
WiFiClient pending; /* candidate waiting to send HELLO */
bool pending_active;
WiFiClient log_client;
uint32_t log_dropped; /* lines skipped because the log connection could not take them */

uint8_t pending_buf[kHeaderLen + kMaxPayload + kCrcLen];
size_t pending_len;
uint32_t pending_since_ms;

uint8_t io_buf[1024];
uint8_t out_buf[2048]; /* STM32 → host: bytes that could not be written wait here, nothing is lost */
size_t out_len;
uint32_t last_status_ms;
uint32_t button_down_ms;
bool wifi_reset_armed;

/* Diagnostic counters: written to the log port every 5 s while a client is connected. */
struct {
    uint32_t tcp_to_uart, uart_to_tcp, short_writes;
    uint32_t max_loop_us, max_tcp_write_us, max_uart_write_us;
    uint32_t last_ms;
} diag;

/* Wi-Fi event ring: disconnects (802.11 reason code) and IP reacquisition, with timestamps. */
enum EvKind : uint8_t { kEvDisconnect = 1, kEvGotIp = 2, kEvRoam = 3 };
struct WifiEvent {
    uint32_t ms;
    uint8_t kind;
    uint8_t reason; /* reason code on disconnect; target's -dBm on roam */
    uint32_t outage_ms; /* on got-IP: outage duration */
};
WifiEvent wifi_events[8];
uint8_t wifi_event_next;
uint32_t wifi_disconnects;
uint32_t wifi_down_since_ms; /* 0: connected */
WiFiEventHandler on_disconnect, on_got_ip;

/* Survives a software restart (RTC user memory; the start belongs to eboot, so from block 64). */
struct RtcStats {
    uint32_t magic;
    uint32_t watchdog_restarts;
    uint32_t last_outage_s; /* duration of the outage that triggered the watchdog */
};
constexpr uint32_t kRtcMagic = 0xDD5E0001, kRtcOffset = 64;
RtcStats rtc;

struct {
    uint32_t weak_since_ms; /* 0: signal is fine */
    uint32_t next_scan_ms;
    bool scanning;
    bool found; /* scan done, target BSSID below (applied in the loop) */
    uint8_t bssid[6];
    int32_t channel, rssi;
} roam;

void record_event(uint8_t kind, uint8_t reason, uint32_t outage_ms)
{
    wifi_events[wifi_event_next] = { millis(), kind, reason, outage_ms };
    wifi_event_next = (wifi_event_next + 1) % (sizeof wifi_events / sizeof wifi_events[0]);
}

void logf(const char *fmt, ...)
{
    if (!log_client.connected()) {
        return;
    }
    char line[176];
    const int prefix = snprintf(line, sizeof line, "[%8lu] ", millis());
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line + prefix, sizeof line - prefix - 1, fmt, ap); /* keep one byte for the newline */
    va_end(ap);
    size_t len = strlen(line);
    line[len++] = '\n';
    /* The log is best effort: when TCP cannot queue the line right now, drop it instead of letting
     * write() wait for an ACK and stall the TCP <-> UART forwarding (the very outages being logged). */
    const size_t room = log_client.availableForWrite();
    if (room < len) {
        log_dropped++;
        return;
    }
    if (log_dropped) {
        char note[48];
        const int n = snprintf(note, sizeof note, "[%8lu] (%u lines dropped)\n", millis(), log_dropped);
        if (room >= len + n) {
            log_client.write(note, n);
            log_dropped = 0;
        }
    }
    log_client.write(line, len);
}

/* CRC-16/CCITT-FALSE, same as firmware/src/crc16.c. */
uint16_t crc16(const uint8_t *d, size_t n, uint16_t crc = 0xFFFF)
{
    while (n--) {
        crc ^= static_cast<uint16_t>(*d++) << 8;
        for (int i = 0; i < 8; i++) {
            crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021) : static_cast<uint16_t>(crc << 1);
        }
    }
    return crc;
}

void drop_client()
{
    client.stop();
    client = WiFiClient();
    client_active = false;
    out_len = 0;
}

void drop_pending()
{
    pending.stop();
    pending = WiFiClient();
    pending_active = false;
}

void send_status(Status s)
{
    uint8_t f[kHeaderLen + 1 + kCrcLen] = { kSync0, kSync1, kMsgStatus, 1, 0, s };
    const uint16_t crc = crc16(&f[2], 4);
    f[6] = static_cast<uint8_t>(crc);
    f[7] = static_cast<uint8_t>(crc >> 8);
    Serial.write(f, sizeof f);
    last_status_ms = millis();
}

/* HELLO in pending_buf: 1 valid, 0 need more bytes, -1 invalid. */
int check_hello()
{
    if (pending_len < kHeaderLen) {
        return 0;
    }
    if (pending_buf[0] != kSync0 || pending_buf[1] != kSync1 || pending_buf[2] != kMsgHello) {
        return -1;
    }
    const size_t len = pending_buf[3] | (pending_buf[4] << 8);
    if (len < 1 || len > kMaxPayload) {
        return -1;
    }
    const size_t total = kHeaderLen + len + kCrcLen;
    if (pending_len < total) {
        return 0;
    }
    const uint16_t got = pending_buf[kHeaderLen + len] | (pending_buf[kHeaderLen + len + 1] << 8);
    if (crc16(&pending_buf[2], 3 + len) != got) {
        return -1;
    }
    /* payload: version u8, token */
    const size_t token_len = len - 1;
    return token_len == strlen(DESKDECK_TOKEN) && memcmp(&pending_buf[kHeaderLen + 1], DESKDECK_TOKEN, token_len) == 0
               ? 1
               : -1;
}

void accept_connections()
{
    if (log_server.hasClient()) {
        log_client.stop();
        log_client = log_server.accept();
        log_client.setTimeout(kTcpWriteTimeoutMs); /* backstop; logf() already checks for room */
        log_dropped = 0;
        logf("log connected; wifi=%s bssid=%s channel=%d rssi=%d dBm sleep=%d phy=%d ip=%s client=%s", WiFi.SSID().c_str(),
             WiFi.BSSIDstr().c_str(), WiFi.channel(), WiFi.RSSI(), static_cast<int>(WiFi.getSleepMode()),
             static_cast<int>(WiFi.getPhyMode()),
             WiFi.localIP().toString().c_str(), client_active ? client.remoteIP().toString().c_str() : "-");
    }
    if (server.hasClient()) {
        if (pending_active) {
            drop_pending();
        }
        pending = server.accept();
        pending_active = true;
        pending.setNoDelay(true);
        pending_len = 0;
        pending_since_ms = millis();
        logf("candidate client %s", pending.remoteIP().toString().c_str());
    }
}

void handle_pending()
{
    if (!pending_active) {
        return;
    }
    if (!pending.connected() || millis() - pending_since_ms > kHelloTimeoutMs) {
        logf("candidate dropped (no HELLO)");
        drop_pending();
        return;
    }
    const size_t room = sizeof pending_buf - pending_len;
    const size_t n = pending.available();
    if (n && room) {
        pending_len += pending.read(&pending_buf[pending_len], n < room ? n : room);
    }
    const int ok = check_hello();
    if (ok < 0) {
        logf("candidate rejected: invalid HELLO or token");
        drop_pending();
    } else if (ok > 0) {
        if (client_active) {
            logf("previous client %s closed", client.remoteIP().toString().c_str());
            drop_client();
        }
        client = pending;
        client.setTimeout(kTcpWriteTimeoutMs);
        client_active = true;
        out_len = 0;
        pending = WiFiClient();
        pending_active = false;
        Serial.write(pending_buf, pending_len); /* HELLO (and anything that followed it) to the STM32 */
        logf("client authenticated: %s", client.remoteIP().toString().c_str());
    }
}

/* If GPIO0 is LOW at boot the ESP enters flash mode, so wait for the button to be released before
 * restarting. Once the hold time is reached the display switches to the portal icon right away. */
void check_wifi_reset_button()
{
    const bool down = digitalRead(kFlashButtonPin) == LOW;
    static bool was_down;
    if (down != was_down) {
        was_down = down;
        logf("FLASH button %s", down ? "pressed" : "released");
    }
    if (wifi_reset_armed) {
        if (!down) {
            logf("erasing Wi-Fi credentials, restarting");
            WiFiManager wm;
            wm.resetSettings();
            delay(100);
            ESP.restart();
        }
        return;
    }
    if (!down) {
        button_down_ms = 0;
    } else if (!button_down_ms) {
        button_down_ms = millis() | 1u;
    } else if (millis() - button_down_ms >= kWifiResetHoldMs) {
        wifi_reset_armed = true;
        logf("FLASH held for 3 s: Wi-Fi credentials will be erased on release");
        drop_client();
        send_status(kPortal);
    }
}

void forward()
{
    size_t n = client.available();
    if (n) {
        n = client.read(io_buf, n < sizeof io_buf ? n : sizeof io_buf);
        const uint32_t t = micros();
        Serial.write(io_buf, n);
        diag.max_uart_write_us = std::max<uint32_t>(diag.max_uart_write_us, micros() - t);
        diag.tcp_to_uart += n;
    }
    /* STM32 → host: move into the output buffer first, then write as much as TCP accepts with a
     * short timeout; the rest waits for the next round. The loop never blocks for long. */
    n = std::min<size_t>(Serial.available(), sizeof out_buf - out_len);
    if (n) {
        out_len += Serial.read(reinterpret_cast<char *>(out_buf + out_len), n);
    }
    const size_t room = client.availableForWrite();
    if (out_len && room) {
        const uint32_t t = micros();
        const size_t w = client.write(out_buf, std::min(out_len, room));
        diag.max_tcp_write_us = std::max<uint32_t>(diag.max_tcp_write_us, micros() - t);
        diag.uart_to_tcp += w;
        if (w < std::min(out_len, room)) {
            diag.short_writes++;
        }
        memmove(out_buf, out_buf + w, out_len - w);
        out_len -= w;
    }
}

void diag_tick(uint32_t loop_us)
{
    diag.max_loop_us = std::max<uint32_t>(diag.max_loop_us, loop_us);
    if (millis() - diag.last_ms < 5000) {
        return;
    }
    logf("tcp→uart %lu uart→tcp %lu short writes %lu | max: loop %lu us, tcp write %lu us, uart write %lu us | heap %u | rssi %d",
         diag.tcp_to_uart, diag.uart_to_tcp, diag.short_writes, diag.max_loop_us, diag.max_tcp_write_us,
         diag.max_uart_write_us, ESP.getFreeHeap(), WiFi.RSSI());
    diag.max_loop_us = diag.max_tcp_write_us = diag.max_uart_write_us = 0;

    diag.last_ms = millis();
}

void wifi_watchdog()
{
    if (!wifi_down_since_ms || millis() - wifi_down_since_ms < kWifiWatchdogMs) {
        return;
    }
    rtc.watchdog_restarts++;
    rtc.last_outage_s = (millis() - wifi_down_since_ms) / 1000;
    ESP.rtcUserMemoryWrite(kRtcOffset, reinterpret_cast<uint32_t *>(&rtc), sizeof rtc);
    ESP.restart();
}

void roam_scan_done(int n)
{
    const String ssid = WiFi.SSID();
    const int32_t cur = WiFi.RSSI();
    const uint8_t *cur_bssid = WiFi.BSSID();
    int best = -1;
    for (int i = 0; i < n; i++) {
        if (WiFi.SSID(i) == ssid && memcmp(WiFi.BSSID(i), cur_bssid, 6) != 0
            && (best < 0 || WiFi.RSSI(i) > WiFi.RSSI(best))) {
            best = i;
        }
    }
    if (best >= 0 && WiFi.RSSI(best) >= cur + kRoamGainDb) {
        memcpy(roam.bssid, WiFi.BSSID(best), 6);
        roam.channel = WiFi.channel(best);
        roam.rssi = WiFi.RSSI(best);
        roam.found = true;
    }
    WiFi.scanDelete();
    roam.scanning = false;
}

void roam_tick()
{
    if (roam.found) {
        roam.found = false;
        logf("roaming: %d dBm → %d dBm (channel %d)", WiFi.RSSI(), roam.rssi, roam.channel);
        record_event(kEvRoam, static_cast<uint8_t>(-roam.rssi), 0);
        /* persistent(false) is required: a persistent begin(…, channel, bssid) corrupted the Wi-Fi
         * credentials in flash and every later boot failed to connect until they were erased and re-entered. */
        WiFi.persistent(false);
        WiFi.begin(WiFi.SSID().c_str(), WiFi.psk().c_str(), roam.channel, roam.bssid);
        WiFi.persistent(true);
        roam.weak_since_ms = 0;
        return;
    }
    const int32_t rssi = WiFi.RSSI();
    if (wifi_down_since_ms || roam.scanning || rssi >= kRoamRssiDbm || rssi > 0) { /* 31: invalid */
        if (rssi >= kRoamRssiDbm) {
            roam.weak_since_ms = 0;
        }
        return;
    }
    const uint32_t now = millis();
    if (!roam.weak_since_ms) {
        roam.weak_since_ms = now | 1u;
    }
    if (now - roam.weak_since_ms < kRoamCheckMs || static_cast<int32_t>(now - roam.next_scan_ms) < 0) {
        return;
    }
    roam.next_scan_ms = now + kRoamRetryMs;
    roam.scanning = true;
    logf("weak signal (%d dBm), looking for a better node", rssi);
    WiFi.scanNetworksAsync(roam_scan_done);
}

void reply_status()
{
    char b[512];
    int n = snprintf(b, sizeof b, "uptime=%us reset=%s wd_restart=%u wd_last_outage=%us disconnects=%u ap=%s ch=%d rssi=%d heap=%u events=",
                     static_cast<uint32_t>(millis() / 1000), ESP.getResetReason().c_str(), rtc.watchdog_restarts, rtc.last_outage_s,
                     wifi_disconnects, WiFi.BSSIDstr().c_str(), WiFi.channel(), WiFi.RSSI(), ESP.getFreeHeap());
    const size_t cnt = sizeof wifi_events / sizeof wifi_events[0];
    for (size_t i = 0; i < cnt && n > 0 && static_cast<size_t>(n) < sizeof b; i++) {
        const WifiEvent &e = wifi_events[(wifi_event_next + i) % cnt];
        if (!e.kind) {
            continue;
        }
        const uint32_t ago = (millis() - e.ms) / 1000;
        n += e.kind == kEvDisconnect ? snprintf(b + n, sizeof b - n, " [-%us disconnect r%u]", ago, e.reason)
             : e.kind == kEvRoam     ? snprintf(b + n, sizeof b - n, " [-%us roam -%udBm]", ago, e.reason)
                                     : snprintf(b + n, sizeof b - n, " [-%us ip %ums]", ago, e.outage_ms);
    }
    udp_echo.beginPacket(udp_echo.remoteIP(), udp_echo.remotePort());
    udp_echo.write(reinterpret_cast<const uint8_t *>(b), strnlen(b, sizeof b));
    udp_echo.endPacket();
}

} // namespace

void setup()
{
    Serial.setRxBufferSize(2048);
    Serial.begin(kBaud);
    Serial.swap(); /* keep ROM boot messages and USB-serial traffic away from the STM32 */
    pinMode(kFlashButtonPin, INPUT_PULLUP);
    delay(20);
    send_status(kWifiConnecting);

    ESP.rtcUserMemoryRead(kRtcOffset, reinterpret_cast<uint32_t *>(&rtc), sizeof rtc);
    if (rtc.magic != kRtcMagic) {
        rtc = { kRtcMagic, 0, 0 };
        ESP.rtcUserMemoryWrite(kRtcOffset, reinterpret_cast<uint32_t *>(&rtc), sizeof rtc);
    }
    wifi_down_since_ms = millis() | 1u;
    on_disconnect = WiFi.onStationModeDisconnected([](const WiFiEventStationModeDisconnected &e) {
        if (!wifi_down_since_ms) {
            wifi_down_since_ms = millis() | 1u;
            wifi_disconnects++;
            record_event(kEvDisconnect, e.reason, 0);
        }
    });
    on_got_ip = WiFi.onStationModeGotIP([](const WiFiEventStationModeGotIP &) {
        record_event(kEvGotIp, 0, wifi_down_since_ms ? millis() - wifi_down_since_ms : 0);
        wifi_down_since_ms = 0;
    });

    WiFi.mode(WIFI_STA);
    WiFi.setSleepMode(WIFI_NONE_SLEEP); /* for latency */
    WiFi.hostname(kHostname);

    /* With saved credentials the portal never opens: otherwise a brief router hiccup left the device
     * in setup mode for minutes. Keep retrying; the portal only opens with no credentials or via FLASH. */
    WiFiManager wm;
    const bool saved = wm.getWiFiIsSaved();
    wm.setHostname(kHostname);
    wm.setConfigPortalTimeout(kPortalTimeoutS);
    wm.setEnableConfigPortal(!saved);
    wm.setAPCallback([](WiFiManager *) { send_status(kPortal); });
    if (!wm.autoConnect(kPortalSsid)) {
        if (!saved) {
            ESP.restart();
        }
        WiFi.mode(WIFI_STA);
        WiFi.setAutoReconnect(true);
        WiFi.begin();
        while (!WiFi.isConnected()) {
            check_wifi_reset_button();
            wifi_watchdog();
            if (millis() - last_status_ms >= kStatusIntervalMs) {
                send_status(kWifiConnecting);
            }
            delay(50);
        }
    }

    /* WiFiManager resets the sleep mode to the default (modem sleep) while connecting, which delayed
     * packets to the ESP by 1-2 s. Turn it off again once connected. */
    WiFi.setSleepMode(WIFI_NONE_SLEEP);

    MDNS.begin(kHostname);
    MDNS.addService(kHostname, "tcp", kPort);
    server.begin();
    server.setNoDelay(true);
    log_server.begin();
    udp_echo.begin(7789);
    send_status(kWaitingMac);
}

void loop()
{
    const uint32_t loop_start = micros();
    diag_tick(0);
    MDNS.update();
    if (const int n = udp_echo.parsePacket(); n > 0 && n <= 64) {
        uint8_t b[64];
        udp_echo.read(b, n);
        if (n == sizeof kStatusQuery - 1 && memcmp(b, kStatusQuery, n) == 0) {
            reply_status();
        } else {
            udp_echo.beginPacket(udp_echo.remoteIP(), udp_echo.remotePort());
            udp_echo.write(b, n);
            udp_echo.endPacket();
        }
    }
    check_wifi_reset_button();
    wifi_watchdog();
    roam_tick();
    if (wifi_reset_armed) {
        return;
    }
    accept_connections();
    handle_pending();

    /* When Wi-Fi drops, TCP only notices minutes later: drop the client right away so the STM32
     * gets STATUS (connecting to Wi-Fi) and shows it. */
    if (client_active && wifi_down_since_ms) {
        logf("Wi-Fi lost, client dropped");
        drop_client();
        send_status(kWifiConnecting);
    }
    if (client_active && !client.connected()) {
        logf("client disconnected");
        drop_client();
        send_status(WiFi.isConnected() ? kWaitingMac : kWifiConnecting);
    }

    if (client_active) {
        forward();
        diag_tick(micros() - loop_start);
        return;
    }

    /* Without a client, anything from the STM32 (LOG "boot", stale CREDIT) is discarded. */
    while (Serial.available()) {
        Serial.read();
    }
    if (millis() - last_status_ms >= kStatusIntervalMs) {
        send_status(WiFi.isConnected() ? kWaitingMac : kWifiConnecting);
    }
}
