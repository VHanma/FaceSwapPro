package org.vaan.faceswap.v2.engine

import android.content.Context
import android.graphics.ImageDecoder
import android.net.Uri
import kotlin.math.roundToInt

class SemanticMaskAnalyzer(private val context: Context) {

    data class Report(
        val skinPercent: Float,
        val hairPercent: Float,
        val eyesPercent: Float,
        val mouthPercent: Float,
        val glassesPercent: Float,
        val foregroundPercent: Float,
    ) {
        val healthy: Boolean
            get() = skinPercent in 5f..85f && (skinPercent + hairPercent) >= 10f

        override fun toString(): String = buildString {
            append(if (healthy) "SEMANTIC MASK PASS" else "SEMANTIC MASK NEEDS WORK")
            append(" • skin=").append(skinPercent.roundToInt()).append('%')
            append(" hair=").append(hairPercent.roundToInt()).append('%')
            append(" eyes=").append(eyesPercent.roundToInt()).append('%')
            append(" mouth/lips=").append(mouthPercent.roundToInt()).append('%')
            if (glassesPercent > 0.1f) {
                append(" glasses=").append(glassesPercent.roundToInt()).append('%')
            }
            append(" protected foreground=").append(foregroundPercent.roundToInt()).append('%')
        }
    }

    fun analyze(uri: Uri): Report {
        val source = ImageDecoder.createSource(context.contentResolver, uri)
        val bitmap = ImageDecoder.decodeBitmap(source) { decoder, _, _ ->
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            decoder.isMutableRequired = false
        }

        val mask = SemanticFaceParser(context).use { parser -> parser.parse(bitmap) }
        bitmap.recycle()

        val counts = IntArray(SemanticFaceParser.NUM_CLASSES)
        for (value in mask.labels) {
            val id = value.toInt() and 0xff
            if (id in counts.indices) counts[id]++
        }
        val total = mask.labels.size.toFloat().coerceAtLeast(1f)
        fun pct(vararg regions: SemanticFaceParser.Region): Float =
            regions.sumOf { counts[it.id] }.toFloat() * 100f / total

        return Report(
            skinPercent = pct(SemanticFaceParser.Region.SKIN),
            hairPercent = pct(SemanticFaceParser.Region.HAIR),
            eyesPercent = pct(
                SemanticFaceParser.Region.LEFT_EYE,
                SemanticFaceParser.Region.RIGHT_EYE,
            ),
            mouthPercent = pct(
                SemanticFaceParser.Region.MOUTH,
                SemanticFaceParser.Region.UPPER_LIP,
                SemanticFaceParser.Region.LOWER_LIP,
            ),
            glassesPercent = pct(SemanticFaceParser.Region.EYEGLASSES),
            foregroundPercent = pct(
                SemanticFaceParser.Region.HAIR,
                SemanticFaceParser.Region.HAT,
                SemanticFaceParser.Region.EYEGLASSES,
                SemanticFaceParser.Region.EARRING,
            ),
        )
    }
}
