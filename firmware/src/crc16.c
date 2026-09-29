#include "proto.h"

/* CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF). Nibble table: 32 B of flash,
 * ~4x faster than bitwise. Python equivalent: binascii.crc_hqx(data, 0xFFFF). */
static const uint16_t k_nibble[16] = {
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
};

uint16_t crc16_ccitt(uint16_t crc, const uint8_t *data, size_t n)
{
    while (n--) {
        uint8_t b = *data++;
        crc = (uint16_t)(crc << 4) ^ k_nibble[(crc >> 12) ^ (b >> 4)];
        crc = (uint16_t)(crc << 4) ^ k_nibble[(crc >> 12) ^ (b & 0x0F)];
    }
    return crc;
}
