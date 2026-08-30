package com.ultimatevideostudio.native3

import android.app.Activity
import android.content.ContentValues
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.StyleSpan
import android.view.Gravity
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.media3.common.C
import androidx.media3.common.Effect
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.common.audio.SpeedProvider
import androidx.media3.common.util.UnstableApi
import androidx.media3.effect.Brightness
import androidx.media3.effect.Contrast
import androidx.media3.effect.HslAdjustment
import androidx.media3.effect.OverlayEffect
import androidx.media3.effect.ScaleAndRotateTransformation
import androidx.media3.effect.StaticOverlaySettings
import androidx.media3.effect.TextOverlay
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.transformer.Composition
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.Effects
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.ProgressHolder
import androidx.media3.transformer.Transformer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import java.io.File
import java.util.ArrayDeque
import java.util.Locale
import kotlin.math.atan2
import kotlin.math.max

@UnstableApi
class MainActivity : Activity() {
    companion object {
        private const val PICK_VIDEO = 701
        private const val PREFS = "uvs_v3_project"
        private const val BG = 0xFF090D10.toInt()
        private const val PANEL = 0xFF11181D.toInt()
        private const val CARD = 0xFF182228.toInt()
        private const val ACCENT = 0xFF31D1DB.toInt()
        private const val MUTED = 0xFF92A2AA.toInt()
    }

    data class EditorState(
        val trimStartMs: Long = 0L,
        val trimEndMs: Long = 0L,
        val brightness: Float = 0f,
        val contrast: Float = 0f,
        val saturation: Float = 0f,
        val rotation: Float = 0f,
        val speed: Float = 1f,
        val text: String = "",
        val textX: Float = 0f,
        val textY: Float = 0f,
        val textScale: Float = 1f,
        val textRotation: Float = 0f,
    )

    private val mainHandler = Handler(Looper.getMainLooper())
    private val prefs by lazy { getSharedPreferences(PREFS, MODE_PRIVATE) }
    private var player: ExoPlayer? = null
    private var playerView: PlayerView? = null
    private var videoUri: Uri? = null
    private var durationMs: Long = 0L
    private var state = EditorState()
    private val undoStack = ArrayDeque<EditorState>()
    private val redoStack = ArrayDeque<EditorState>()
    private var transformer: Transformer? = null
    private var exportTemp: File? = null
    private var exportProgress: ProgressBar? = null
    private var exportStatus: TextView? = null

    private lateinit var root: LinearLayout
    private lateinit var canvas: FrameLayout
    private lateinit var textOverlayView: GestureTextView
    private lateinit var seekBar: SeekBar
    private lateinit var timeNow: TextView
    private lateinit var timeEnd: TextView
    private lateinit var toolPanel: LinearLayout
    private lateinit var timelineStrip: LinearLayout
    private lateinit var playButton: Button
    private var effectUpdatePending = false
    private var updateLoopStarted = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = BG
        window.navigationBarColor = BG
        showHome()
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun makeButton(text: String, accent: Boolean = false): Button = Button(this).apply {
        this.text = text
        setTextColor(if (accent) Color.rgb(4, 30, 34) else Color.WHITE)
        setBackgroundColor(if (accent) ACCENT else CARD)
        isAllCaps = false
        textSize = 13f
        setPadding(dp(10), 0, dp(10), 0)
    }

    private fun label(text: String, size: Float = 13f, color: Int = Color.WHITE): TextView = TextView(this).apply {
        this.text = text
        setTextColor(color)
        textSize = size
        gravity = Gravity.CENTER_VERTICAL
    }

    private fun showHome() {
        releasePlayer()
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(BG)
            setPadding(dp(18), dp(18), dp(18), dp(24))
        }
        setContentView(root)

        val title = label("ULTIMATE  STUDIO", 24f).apply { setTypeface(typeface, Typeface.BOLD) }
        root.addView(title, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))

        val sub = label("Native Android editor • live means live", 13f, MUTED)
        root.addView(sub, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(34)))

        val createCard = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(20), dp(20), dp(20), dp(20))
            setBackgroundColor(Color.rgb(17, 63, 68))
        }
        val createTitle = label("＋  CREATE PROJECT", 23f).apply {
            gravity = Gravity.CENTER
            setTypeface(typeface, Typeface.BOLD)
        }
        createCard.addView(createTitle, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(70)))
        createCard.addView(label("Pick a video. Playback starts in the real editor, not a render preview.", 13f, Color.rgb(190, 224, 226)).apply { gravity = Gravity.CENTER }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(50)))
        val create = makeButton("Create", true)
        create.setOnClickListener { pickVideo() }
        createCard.addView(create, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(54)))
        root.addView(createCard, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(220)).apply { topMargin = dp(12) })

        root.addView(label("PROJECTS", 16f).apply { setTypeface(typeface, Typeface.BOLD) }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(60)))

        val saved = prefs.getString("uri", null)
        if (saved != null) {
            val resumeCard = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(16), dp(14), dp(16), dp(14))
                setBackgroundColor(PANEL)
            }
            resumeCard.addView(label("Last autosaved project", 16f), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(34)))
            resumeCard.addView(label(saved.takeLast(54), 11f, MUTED), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(34)))
            val resume = makeButton("Resume project")
            resume.setOnClickListener {
                val uri = Uri.parse(saved)
                videoUri = uri
                state = loadState()
                showEditor(uri, restore = true)
            }
            resumeCard.addView(resume, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(50)))
            root.addView(resumeCard, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(145)))
        } else {
            root.addView(label("No projects yet. Your last project will autosave here.", 13f, MUTED), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(70)))
        }
    }

    private fun pickVideo() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "video/*"
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
        }
        startActivityForResult(intent, PICK_VIDEO)
    }

    @Deprecated("Compatibility path intentionally used for a framework-only Activity")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != PICK_VIDEO || resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        try {
            contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        } catch (_: Exception) {
        }
        videoUri = uri
        state = EditorState()
        undoStack.clear()
        redoStack.clear()
        prefs.edit().clear().putString("uri", uri.toString()).apply()
        showEditor(uri, restore = false)
    }

    private fun showEditor(uri: Uri, restore: Boolean) {
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(BG)
        }
        setContentView(root)

        val top = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(8), dp(4), dp(8), dp(4))
            setBackgroundColor(PANEL)
        }
        val back = makeButton("‹ Home")
        back.setOnClickListener { saveState(); showHome() }
        top.addView(back, LinearLayout.LayoutParams(dp(82), dp(46)))
        top.addView(label("ULTIMATE STUDIO", 16f).apply { setTypeface(typeface, Typeface.BOLD); setPadding(dp(10), 0, 0, 0) }, LinearLayout.LayoutParams(0, dp(46), 1f))
        val undo = makeButton("↶")
        undo.setOnClickListener { undo() }
        top.addView(undo, LinearLayout.LayoutParams(dp(48), dp(46)))
        val redo = makeButton("↷")
        redo.setOnClickListener { redo() }
        top.addView(redo, LinearLayout.LayoutParams(dp(48), dp(46)))
        val export = makeButton("EXPORT", true)
        export.setOnClickListener { startExport() }
        top.addView(export, LinearLayout.LayoutParams(dp(92), dp(46)))
        root.addView(top, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(54)))

        canvas = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        playerView = PlayerView(this).apply {
            useController = false
            resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
            setShutterBackgroundColor(Color.BLACK)
        }
        canvas.addView(playerView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        textOverlayView = GestureTextView(this).apply {
            visibility = View.GONE
            setTextColor(Color.WHITE)
            textSize = 28f
            setTypeface(typeface, Typeface.BOLD)
            setShadowLayer(8f, 0f, 2f, Color.BLACK)
            gravity = Gravity.CENTER
            setPadding(dp(10), dp(6), dp(10), dp(6))
            onChanged = { x, y, scale, rotation ->
                state = state.copy(textX = x, textY = y, textScale = scale, textRotation = rotation)
                saveState()
            }
            onGestureBegin = { pushUndo() }
        }
        canvas.addView(textOverlayView, FrameLayout.LayoutParams(dp(220), dp(90), Gravity.CENTER))
        val canvasHeight = max(dp(260), (resources.displayMetrics.widthPixels * 9f / 16f).toInt())
        root.addView(canvas, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, canvasHeight))

        val seekRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(8), 0, dp(8), 0)
            setBackgroundColor(PANEL)
        }
        timeNow = label("0:00", 11f, MUTED).apply { gravity = Gravity.CENTER }
        seekRow.addView(timeNow, LinearLayout.LayoutParams(dp(52), dp(38)))
        seekBar = SeekBar(this).apply { max = 1000 }
        seekRow.addView(seekBar, LinearLayout.LayoutParams(0, dp(38), 1f))
        timeEnd = label("0:00", 11f, MUTED).apply { gravity = Gravity.CENTER }
        seekRow.addView(timeEnd, LinearLayout.LayoutParams(dp(52), dp(38)))
        root.addView(seekRow, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(42)))

        seekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?, progress: Int, fromUser: Boolean) {
                if (!fromUser || durationMs <= 0L) return
                val target = durationMs * progress / 1000L
                player?.seekTo(target.coerceIn(state.trimStartMs, effectiveTrimEnd()))
                timeNow.text = formatTime(target)
            }
            override fun onStartTrackingTouch(bar: SeekBar?) { player?.pause() }
            override fun onStopTrackingTouch(bar: SeekBar?) {}
        })

        val timelineScroll = HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            setBackgroundColor(Color.rgb(12, 17, 21))
        }
        timelineStrip = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        timelineScroll.addView(timelineStrip, ViewGroup.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(64)))
        root.addView(timelineScroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(66)))

        val transport = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(dp(6), dp(3), dp(6), dp(3))
            setBackgroundColor(PANEL)
        }
        val prev = makeButton("−33 ms")
        prev.setOnClickListener { stepBy(-33) }
        transport.addView(prev, LinearLayout.LayoutParams(0, dp(44), 1f))
        playButton = makeButton("▶")
        playButton.setOnClickListener {
            player?.let { p -> if (p.isPlaying) p.pause() else p.play() }
        }
        transport.addView(playButton, LinearLayout.LayoutParams(0, dp(44), 1f).apply { leftMargin = dp(5); rightMargin = dp(5) })
        val next = makeButton("+33 ms")
        next.setOnClickListener { stepBy(33) }
        transport.addView(next, LinearLayout.LayoutParams(0, dp(44), 1f))
        root.addView(transport, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(50)))

        val toolsScroll = HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            setBackgroundColor(BG)
        }
        val tools = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(dp(6), dp(5), dp(6), dp(5)) }
        listOf("Trim", "Adjust", "Rotate", "Speed", "Text", "Reset").forEach { name ->
            val b = makeButton(name)
            b.setOnClickListener {
                when (name) {
                    "Trim" -> showTrimTools()
                    "Adjust" -> showAdjustTools()
                    "Rotate" -> showRotateTools()
                    "Speed" -> showSpeedTools()
                    "Text" -> showTextTools()
                    "Reset" -> resetEdits()
                }
            }
            tools.addView(b, LinearLayout.LayoutParams(dp(92), dp(48)).apply { rightMargin = dp(5) })
        }
        toolsScroll.addView(tools)
        root.addView(toolsScroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(58)))

        toolPanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(8), dp(12), dp(8))
            setBackgroundColor(PANEL)
        }
        root.addView(toolPanel, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        showTrimTools()

        initializePlayer(uri, restore)
        generateTimeline(uri)
        startUiLoop()
    }

    private fun initializePlayer(uri: Uri, restore: Boolean) {
        releasePlayer()
        val p = ExoPlayer.Builder(this).build()
        player = p
        playerView?.player = p
        p.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                if (playbackState == Player.STATE_READY) {
                    durationMs = if (p.duration > 0) p.duration else 0L
                    if (!restore || state.trimEndMs <= 0L || state.trimEndMs > durationMs) {
                        state = state.copy(trimEndMs = durationMs)
                    }
                    timeEnd.text = formatTime(durationMs)
                    p.seekTo(state.trimStartMs.coerceAtMost(durationMs))
                    p.playbackParameters = PlaybackParameters(state.speed)
                    applyVideoEffectsNow()
                    syncOverlayFromState()
                    saveState()
                }
                if (playbackState == Player.STATE_ENDED) {
                    p.pause()
                    p.seekTo(state.trimStartMs)
                }
            }
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                playButton.text = if (isPlaying) "Ⅱ" else "▶"
            }
            override fun onPlayerError(error: PlaybackException) {
                toast("Playback error: ${error.errorCodeName}")
            }
        })
        p.setMediaItem(MediaItem.fromUri(uri))
        p.prepare()
    }

    private fun releasePlayer() {
        playerView?.player = null
        player?.release()
        player = null
        playerView = null
    }

    private fun startUiLoop() {
        if (updateLoopStarted) return
        updateLoopStarted = true
        mainHandler.post(object : Runnable {
            override fun run() {
                val p = player
                if (p != null && durationMs > 0L) {
                    val pos = p.currentPosition.coerceAtLeast(0L)
                    val end = effectiveTrimEnd()
                    if (p.isPlaying && pos >= end) {
                        p.pause()
                        p.seekTo(state.trimStartMs)
                    }
                    if (!seekBar.isPressed) seekBar.progress = ((pos * 1000L) / durationMs).toInt().coerceIn(0, 1000)
                    timeNow.text = formatTime(pos)
                }
                mainHandler.postDelayed(this, 33)
            }
        })
    }

    private fun effectiveTrimEnd(): Long = if (state.trimEndMs > 0L) state.trimEndMs else durationMs

    private fun stepBy(deltaMs: Long) {
        val p = player ?: return
        p.pause()
        val target = (p.currentPosition + deltaMs).coerceIn(state.trimStartMs, effectiveTrimEnd())
        p.seekTo(target)
    }

    private fun formatTime(ms: Long): String {
        val total = (ms / 1000L).coerceAtLeast(0L)
        return String.format(Locale.US, "%d:%02d", total / 60, total % 60)
    }

    private fun showTrimTools() {
        toolPanel.removeAllViews()
        toolPanel.addView(label("TRIM", 15f).apply { setTypeface(typeface, Typeface.BOLD) }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(34)))
        toolPanel.addView(label("Start", 12f, MUTED), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(26)))
        val start = SeekBar(this).apply { max = 1000; progress = if (durationMs > 0) ((state.trimStartMs * 1000) / durationMs).toInt() else 0 }
        toolPanel.addView(start, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(42)))
        toolPanel.addView(label("End", 12f, MUTED), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(26)))
        val end = SeekBar(this).apply { max = 1000; progress = if (durationMs > 0) ((effectiveTrimEnd() * 1000) / durationMs).toInt() else 1000 }
        toolPanel.addView(end, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(42)))

        var captured = false
        val listener = object : SeekBar.OnSeekBarChangeListener {
            override fun onStartTrackingTouch(seekBar: SeekBar?) { if (!captured) { pushUndo(); captured = true } }
            override fun onStopTrackingTouch(seekBar: SeekBar?) { captured = false; saveState() }
            override fun onProgressChanged(bar: SeekBar?, progress: Int, fromUser: Boolean) {
                if (!fromUser || durationMs <= 0L) return
                if (bar === start) {
                    val value = durationMs * progress / 1000L
                    state = state.copy(trimStartMs = value.coerceAtMost(effectiveTrimEnd() - 100L).coerceAtLeast(0L))
                    player?.seekTo(state.trimStartMs)
                } else {
                    val value = durationMs * progress / 1000L
                    state = state.copy(trimEndMs = value.coerceAtLeast(state.trimStartMs + 100L).coerceAtMost(durationMs))
                    player?.seekTo(state.trimEndMs)
                }
            }
        }
        start.setOnSeekBarChangeListener(listener)
        end.setOnSeekBarChangeListener(listener)
    }

    private fun showAdjustTools() {
        toolPanel.removeAllViews()
        toolPanel.addView(label("ADJUST • GPU LIVE", 15f).apply { setTypeface(typeface, Typeface.BOLD) }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(34)))
        addEffectSlider("Brightness", ((state.brightness + 1f) * 100).toInt(), 200) { v -> state = state.copy(brightness = v / 100f - 1f) }
        addEffectSlider("Contrast", ((state.contrast + 1f) * 100).toInt(), 200) { v -> state = state.copy(contrast = v / 100f - 1f) }
        addEffectSlider("Saturation", (state.saturation + 100f).toInt(), 200) { v -> state = state.copy(saturation = v.toFloat() - 100f) }
    }

    private fun addEffectSlider(name: String, progress: Int, max: Int, setValue: (Int) -> Unit) {
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        row.addView(label(name, 12f, MUTED), LinearLayout.LayoutParams(dp(90), dp(40)))
        val valueLabel = label("", 11f, Color.WHITE).apply { gravity = Gravity.CENTER }
        val slider = SeekBar(this).apply { this.max = max; this.progress = progress.coerceIn(0, max) }
        row.addView(slider, LinearLayout.LayoutParams(0, dp(40), 1f))
        row.addView(valueLabel, LinearLayout.LayoutParams(dp(48), dp(40)))
        toolPanel.addView(row, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(44)))
        var captured = false
        slider.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onStartTrackingTouch(seekBar: SeekBar?) { if (!captured) { pushUndo(); captured = true } }
            override fun onStopTrackingTouch(seekBar: SeekBar?) { captured = false; saveState() }
            override fun onProgressChanged(seekBar: SeekBar?, p: Int, fromUser: Boolean) {
                setValue(p)
                valueLabel.text = when (name) {
                    "Saturation" -> "${p - 100}"
                    else -> String.format(Locale.US, "%.2f", p / 100f - 1f)
                }
                if (fromUser) scheduleEffectsUpdate()
            }
        })
        slider.progress = progress.coerceIn(0, max)
    }

    private fun showRotateTools() {
        toolPanel.removeAllViews()
        toolPanel.addView(label("ROTATE", 15f).apply { setTypeface(typeface, Typeface.BOLD) }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(34)))
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        listOf("−90°" to -90f, "0°" to 0f, "+90°" to 90f, "180°" to 180f).forEach { (text, deg) ->
            val b = makeButton(text)
            b.setOnClickListener { pushUndo(); state = state.copy(rotation = deg); applyVideoEffectsNow(); saveState() }
            row.addView(b, LinearLayout.LayoutParams(0, dp(52), 1f).apply { rightMargin = dp(4) })
        }
        toolPanel.addView(row, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(58)))
        toolPanel.addView(label("Rotation is applied by the same Media3 effect in preview and export.", 12f, MUTED), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(50)))
    }

    private fun showSpeedTools() {
        toolPanel.removeAllViews()
        toolPanel.addView(label("SPEED", 15f).apply { setTypeface(typeface, Typeface.BOLD) }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(34)))
        val value = label(String.format(Locale.US, "%.2fx", state.speed), 16f).apply { gravity = Gravity.CENTER }
        toolPanel.addView(value, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(34)))
        val slider = SeekBar(this).apply {
            max = 375
            progress = ((state.speed - 0.25f) * 100f).toInt().coerceIn(0, 375)
        }
        toolPanel.addView(slider, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(48)))
        var captured = false
        slider.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onStartTrackingTouch(seekBar: SeekBar?) { if (!captured) { pushUndo(); captured = true } }
            override fun onStopTrackingTouch(seekBar: SeekBar?) { captured = false; saveState() }
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                val speed = (0.25f + progress / 100f).coerceIn(0.25f, 4f)
                state = state.copy(speed = speed)
                value.text = String.format(Locale.US, "%.2fx", speed)
                if (fromUser) player?.playbackParameters = PlaybackParameters(speed)
            }
        })
    }

    private fun showTextTools() {
        toolPanel.removeAllViews()
        toolPanel.addView(label("TEXT • DRAG / PINCH / TWIST ON VIDEO", 15f).apply { setTypeface(typeface, Typeface.BOLD) }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(34)))
        val input = EditText(this).apply {
            setText(state.text)
            setTextColor(Color.WHITE)
            setHintTextColor(MUTED)
            hint = "Type text"
            setBackgroundColor(CARD)
            setPadding(dp(10), 0, dp(10), 0)
            singleLine = true
        }
        toolPanel.addView(input, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(50)))
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        val apply = makeButton("Apply text", true)
        apply.setOnClickListener {
            pushUndo()
            state = state.copy(text = input.text.toString())
            syncOverlayFromState()
            saveState()
        }
        row.addView(apply, LinearLayout.LayoutParams(0, dp(50), 1f).apply { rightMargin = dp(5) })
        val remove = makeButton("Remove")
        remove.setOnClickListener {
            pushUndo()
            state = state.copy(text = "")
            syncOverlayFromState()
            saveState()
        }
        row.addView(remove, LinearLayout.LayoutParams(0, dp(50), 1f))
        toolPanel.addView(row, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
    }

    private fun buildVideoEffects(includeText: Boolean): List<Effect> {
        val list = mutableListOf<Effect>()
        if (state.brightness != 0f) list.add(Brightness(state.brightness.coerceIn(-1f, 1f)))
        if (state.contrast != 0f) list.add(Contrast(state.contrast.coerceIn(-1f, 1f)))
        if (state.saturation != 0f) list.add(HslAdjustment.Builder().adjustSaturation(state.saturation.coerceIn(-100f, 100f)).build())
        if ((state.rotation % 360f) != 0f) list.add(ScaleAndRotateTransformation.Builder().setRotationDegrees((state.rotation % 360f + 360f) % 360f).build())
        if (includeText && state.text.isNotBlank()) {
            val styled = SpannableString(state.text).apply {
                setSpan(ForegroundColorSpan(Color.WHITE), 0, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                setSpan(StyleSpan(Typeface.BOLD), 0, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
            val settings = StaticOverlaySettings.Builder()
                .setOverlayFrameAnchor(0f, 0f)
                .setBackgroundFrameAnchor(state.textX.coerceIn(-1f, 1f), state.textY.coerceIn(-1f, 1f))
                .setScale(state.textScale.coerceIn(0.2f, 5f), state.textScale.coerceIn(0.2f, 5f))
                .setRotationDegrees(state.textRotation)
                .build()
            val overlay = TextOverlay.createStaticTextOverlay(styled, settings)
            list.add(OverlayEffect(listOf(overlay)))
        }
        return list
    }

    private fun scheduleEffectsUpdate() {
        if (effectUpdatePending) return
        effectUpdatePending = true
        mainHandler.postDelayed({
            effectUpdatePending = false
            applyVideoEffectsNow()
            saveState()
        }, 24)
    }

    private fun applyVideoEffectsNow() {
        try {
            player?.setVideoEffects(buildVideoEffects(includeText = false))
        } catch (e: Exception) {
            toast("Live effect error: ${e.message ?: e.javaClass.simpleName}")
        }
    }

    private fun syncOverlayFromState() {
        if (state.text.isBlank()) {
            textOverlayView.visibility = View.GONE
            return
        }
        textOverlayView.visibility = View.VISIBLE
        textOverlayView.text = state.text
        textOverlayView.post {
            val cx = ((state.textX + 1f) * 0.5f * canvas.width)
            val cy = ((1f - state.textY) * 0.5f * canvas.height)
            textOverlayView.x = cx - textOverlayView.width / 2f
            textOverlayView.y = cy - textOverlayView.height / 2f
            textOverlayView.scaleX = state.textScale
            textOverlayView.scaleY = state.textScale
            textOverlayView.rotation = -state.textRotation
        }
    }

    private fun generateTimeline(uri: Uri) {
        timelineStrip.removeAllViews()
        repeat(10) {
            timelineStrip.addView(ImageView(this).apply {
                setBackgroundColor(CARD)
                scaleType = ImageView.ScaleType.CENTER_CROP
            }, LinearLayout.LayoutParams(dp(82), dp(60)).apply { rightMargin = dp(2) })
        }
        Thread {
            val retriever = MediaMetadataRetriever()
            try {
                retriever.setDataSource(this, uri)
                val d = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
                for (i in 0 until 10) {
                    val tUs = if (d > 0) (d * 1000L * i / 9L) else 0L
                    val bmp = retriever.getFrameAtTime(tUs, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                    if (bmp != null) runOnUiThread {
                        (timelineStrip.getChildAt(i) as? ImageView)?.setImageBitmap(bmp)
                    }
                }
            } catch (_: Exception) {
            } finally {
                try { retriever.release() } catch (_: Exception) {}
            }
        }.start()
    }

    private fun pushUndo() {
        if (undoStack.peekLast() == state) return
        undoStack.addLast(state)
        while (undoStack.size > 50) undoStack.removeFirst()
        redoStack.clear()
    }

    private fun undo() {
        if (undoStack.isEmpty()) return
        redoStack.addLast(state)
        state = undoStack.removeLast()
        applyStateToUi()
    }

    private fun redo() {
        if (redoStack.isEmpty()) return
        undoStack.addLast(state)
        state = redoStack.removeLast()
        applyStateToUi()
    }

    private fun applyStateToUi() {
        applyVideoEffectsNow()
        player?.playbackParameters = PlaybackParameters(state.speed)
        player?.seekTo(state.trimStartMs.coerceAtMost(effectiveTrimEnd()))
        syncOverlayFromState()
        saveState()
        showTrimTools()
    }

    private fun resetEdits() {
        pushUndo()
        state = EditorState(trimStartMs = 0L, trimEndMs = durationMs)
        applyStateToUi()
    }

    private fun saveState() {
        val uri = videoUri ?: return
        prefs.edit()
            .putString("uri", uri.toString())
            .putLong("trimStart", state.trimStartMs)
            .putLong("trimEnd", state.trimEndMs)
            .putFloat("brightness", state.brightness)
            .putFloat("contrast", state.contrast)
            .putFloat("saturation", state.saturation)
            .putFloat("rotation", state.rotation)
            .putFloat("speed", state.speed)
            .putString("text", state.text)
            .putFloat("textX", state.textX)
            .putFloat("textY", state.textY)
            .putFloat("textScale", state.textScale)
            .putFloat("textRotation", state.textRotation)
            .apply()
    }

    private fun loadState(): EditorState = EditorState(
        trimStartMs = prefs.getLong("trimStart", 0L),
        trimEndMs = prefs.getLong("trimEnd", 0L),
        brightness = prefs.getFloat("brightness", 0f),
        contrast = prefs.getFloat("contrast", 0f),
        saturation = prefs.getFloat("saturation", 0f),
        rotation = prefs.getFloat("rotation", 0f),
        speed = prefs.getFloat("speed", 1f).coerceIn(0.25f, 4f),
        text = prefs.getString("text", "") ?: "",
        textX = prefs.getFloat("textX", 0f),
        textY = prefs.getFloat("textY", 0f),
        textScale = prefs.getFloat("textScale", 1f),
        textRotation = prefs.getFloat("textRotation", 0f),
    )

    private fun startExport() {
        val uri = videoUri ?: run { toast("Choose a video first"); return }
        if (transformer != null) { toast("Export already running"); return }
        player?.pause()
        saveState()

        toolPanel.removeAllViews()
        exportStatus = label("Preparing native export…", 14f).apply { gravity = Gravity.CENTER }
        toolPanel.addView(exportStatus, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(44)))
        exportProgress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { max = 100 }
        toolPanel.addView(exportProgress, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(42)))
        val cancel = makeButton("Cancel export")
        cancel.setOnClickListener { transformer?.cancel(); transformer = null; exportStatus?.text = "Export cancelled" }
        toolPanel.addView(cancel, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(48)))

        val clipping = MediaItem.ClippingConfiguration.Builder()
            .setStartPositionMs(state.trimStartMs.coerceAtLeast(0L))
            .setEndPositionMs(effectiveTrimEnd().coerceAtLeast(state.trimStartMs + 1L))
            .build()
        val mediaItem = MediaItem.Builder().setUri(uri).setClippingConfiguration(clipping).build()
        val editedBuilder = EditedMediaItem.Builder(mediaItem)
            .setEffects(Effects(emptyList(), buildVideoEffects(includeText = true)))

        if (state.speed != 1f) {
            val s = state.speed
            editedBuilder.setSpeed(object : SpeedProvider {
                override fun getSpeed(timeUs: Long): Float = s
                override fun getNextSpeedChangeTimeUs(timeUs: Long): Long = C.TIME_UNSET
            })
        }

        val edited = editedBuilder.build()
        val temp = File(cacheDir, "uvs_v3_${System.currentTimeMillis()}.mp4")
        exportTemp = temp
        val listener = object : Transformer.Listener {
            override fun onCompleted(composition: Composition, result: ExportResult) {
                val file = exportTemp
                transformer = null
                if (file == null || !file.exists() || file.length() <= 0L) {
                    exportStatus?.text = "Export failed: empty file"
                    return
                }
                try {
                    val published = publishToGallery(file)
                    exportProgress?.progress = 100
                    exportStatus?.text = "Saved to Movies/UltimateVideoStudio"
                    toast("Export saved")
                    file.delete()
                    exportTemp = null
                    if (published != null) {
                        // Keep project open; gallery URI is deliberately not auto-launched.
                    }
                } catch (e: Exception) {
                    exportStatus?.text = "Save failed: ${e.message}"
                }
            }
            override fun onError(composition: Composition, result: ExportResult, exception: ExportException) {
                transformer = null
                exportStatus?.text = "Export error: ${exception.message ?: "unknown"}"
            }
        }

        try {
            transformer = Transformer.Builder(this)
                .setVideoMimeType(MimeTypes.VIDEO_H264)
                .setAudioMimeType(MimeTypes.AUDIO_AAC)
                .addListener(listener)
                .build()
            transformer!!.start(edited, temp.absolutePath)
            pollExportProgress()
        } catch (e: Exception) {
            transformer = null
            exportStatus?.text = "Export could not start: ${e.message}"
        }
    }

    private fun pollExportProgress() {
        val holder = ProgressHolder()
        mainHandler.post(object : Runnable {
            override fun run() {
                val t = transformer ?: return
                try {
                    val progressState = t.getProgress(holder)
                    if (progressState == Transformer.PROGRESS_STATE_AVAILABLE) {
                        exportProgress?.progress = holder.progress
                        exportStatus?.text = "Exporting ${holder.progress}%"
                    }
                    mainHandler.postDelayed(this, 400)
                } catch (_: Exception) {
                }
            }
        })
    }

    private fun publishToGallery(file: File): Uri? {
        val name = "UltimateStudio_${System.currentTimeMillis()}.mp4"
        val values = ContentValues().apply {
            put(MediaStore.Video.Media.DISPLAY_NAME, name)
            put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
            put(MediaStore.Video.Media.RELATIVE_PATH, "Movies/UltimateVideoStudio")
        }
        val uri = contentResolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values) ?: return null
        try {
            contentResolver.openOutputStream(uri, "w")!!.use { out ->
                file.inputStream().use { input -> input.copyTo(out, 1024 * 1024) }
            }
            return uri
        } catch (e: Exception) {
            contentResolver.delete(uri, null, null)
            throw e
        }
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    override fun onPause() {
        super.onPause()
        saveState()
        player?.pause()
    }

    override fun onDestroy() {
        transformer?.cancel()
        transformer = null
        releasePlayer()
        super.onDestroy()
    }

    inner class GestureTextView(context: android.content.Context) : TextView(context) {
        var onChanged: ((Float, Float, Float, Float) -> Unit)? = null
        var onGestureBegin: (() -> Unit)? = null
        private var lastRawX = 0f
        private var lastRawY = 0f
        private var previousAngle = 0f
        private var rotating = false
        private var gestureStarted = false
        private val scaleDetector = ScaleGestureDetector(context, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScaleBegin(detector: ScaleGestureDetector): Boolean {
                startGestureOnce()
                return true
            }
            override fun onScale(detector: ScaleGestureDetector): Boolean {
                val next = (scaleX * detector.scaleFactor).coerceIn(0.2f, 5f)
                scaleX = next
                scaleY = next
                notifyState()
                return true
            }
        })

        init {
            setOnTouchListener { _, event ->
                scaleDetector.onTouchEvent(event)
                when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        parent.requestDisallowInterceptTouchEvent(true)
                        lastRawX = event.rawX
                        lastRawY = event.rawY
                        rotating = false
                        gestureStarted = false
                        startGestureOnce()
                        true
                    }
                    MotionEvent.ACTION_POINTER_DOWN -> {
                        if (event.pointerCount >= 2) {
                            previousAngle = angle(event)
                            rotating = true
                            startGestureOnce()
                        }
                        true
                    }
                    MotionEvent.ACTION_MOVE -> {
                        if (event.pointerCount >= 2 && rotating) {
                            val a = angle(event)
                            val delta = a - previousAngle
                            rotation += delta
                            previousAngle = a
                            notifyState()
                        } else if (!scaleDetector.isInProgress) {
                            val dx = event.rawX - lastRawX
                            val dy = event.rawY - lastRawY
                            x = (x + dx).coerceIn(-width * 0.4f, canvas.width - width * 0.6f)
                            y = (y + dy).coerceIn(-height * 0.4f, canvas.height - height * 0.6f)
                            lastRawX = event.rawX
                            lastRawY = event.rawY
                            notifyState()
                        }
                        true
                    }
                    MotionEvent.ACTION_POINTER_UP -> { rotating = false; true }
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                        parent.requestDisallowInterceptTouchEvent(false)
                        notifyState()
                        true
                    }
                    else -> true
                }
            }
        }

        private fun startGestureOnce() {
            if (!gestureStarted) {
                gestureStarted = true
                onGestureBegin?.invoke()
            }
        }

        private fun angle(event: MotionEvent): Float {
            if (event.pointerCount < 2) return 0f
            val dx = event.getX(1) - event.getX(0)
            val dy = event.getY(1) - event.getY(0)
            return Math.toDegrees(atan2(dy.toDouble(), dx.toDouble())).toFloat()
        }

        private fun notifyState() {
            if (canvas.width <= 0 || canvas.height <= 0) return
            val centerX = x + width / 2f
            val centerY = y + height / 2f
            val nx = ((centerX / canvas.width) * 2f - 1f).coerceIn(-1f, 1f)
            val ny = (1f - (centerY / canvas.height) * 2f).coerceIn(-1f, 1f)
            onChanged?.invoke(nx, ny, scaleX, -rotation)
        }
    }
}
