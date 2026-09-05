package com.giathinh.hashplay

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

private val DarkColors = darkColorScheme(
    primary = androidx.compose.ui.graphics.Color(0xFF00E6B8),
    background = androidx.compose.ui.graphics.Color(0xFF0A0C12),
    surface = androidx.compose.ui.graphics.Color(0xFF12151F),
    onBackground = androidx.compose.ui.graphics.Color(0xFFE8EAF2),
    onSurface = androidx.compose.ui.graphics.Color(0xFFE8EAF2)
)

@Composable
fun HashPlayTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColors,
        content = content
    )
}
