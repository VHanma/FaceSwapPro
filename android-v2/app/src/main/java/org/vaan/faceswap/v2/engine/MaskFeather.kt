package org.vaan.faceswap.v2.engine

/** Small allocation-conscious separable blur for 8-bit alpha masks. */
object MaskFeather {
    fun feather(mask: ByteArray, width: Int, height: Int, radius: Int, passes: Int = 2): ByteArray {
        require(mask.size == width * height)
        if (radius <= 0 || passes <= 0) return mask.copyOf()
        var current = mask.copyOf()
        repeat(passes) {
            current = boxBlur(current, width, height, radius)
        }
        return current
    }

    private fun boxBlur(input: ByteArray, width: Int, height: Int, radius: Int): ByteArray {
        val temp = IntArray(input.size)
        val output = ByteArray(input.size)
        val diameter = radius * 2 + 1

        for (y in 0 until height) {
            var sum = 0
            for (x in -radius..radius) {
                val clamped = x.coerceIn(0, width - 1)
                sum += input[y * width + clamped].toInt() and 0xff
            }
            for (x in 0 until width) {
                temp[y * width + x] = sum / diameter
                val leaving = (x - radius).coerceIn(0, width - 1)
                val entering = (x + radius + 1).coerceIn(0, width - 1)
                sum -= input[y * width + leaving].toInt() and 0xff
                sum += input[y * width + entering].toInt() and 0xff
            }
        }

        for (x in 0 until width) {
            var sum = 0
            for (y in -radius..radius) {
                val clamped = y.coerceIn(0, height - 1)
                sum += temp[clamped * width + x]
            }
            for (y in 0 until height) {
                output[y * width + x] = (sum / diameter).coerceIn(0, 255).toByte()
                val leaving = (y - radius).coerceIn(0, height - 1)
                val entering = (y + radius + 1).coerceIn(0, height - 1)
                sum -= temp[leaving * width + x]
                sum += temp[entering * width + x]
            }
        }
        return output
    }
}
