def khz_to_spi(khz):
    if khz <= 0:
        raise ValueError("kHz must be greater than 0")
    return 1 / (khz * 1_000)


def spi_to_khz(spi):
    if spi <= 0:
        raise ValueError("seconds per instruction must be greater than 0")
    return 1 / spi / 1_000

