package com.ultimatevideostudio.native3

import android.widget.EditText

/** Kotlin-friendly compatibility property backed by TextView.setSingleLine(). */
var EditText.singleLine: Boolean
    get() = maxLines == 1
    set(value) {
        setSingleLine(value)
    }
