package com.example.sharpentry

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private val Navy = Color(0xFF0A1020)
private val Panel = Color(0xFF141E31)
private val Mint = Color(0xFF43E0B5)
private val Muted = Color(0xFF9BA9BF)
private val Amber = Color(0xFFFFC66D)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = Navy
                ) {
                    SharpEntryScreen()
                }
            }
        }
    }
}

@Composable
private fun SharpEntryScreen() {
    var selectedTab by remember { mutableStateOf("Lab") }
    var botEnabled by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Navy)
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text("SHARPENTRY", color = Color.White, fontSize = 23.sp, fontWeight = FontWeight.Black)
                Text("TRADING INTELLIGENCE", color = Muted, fontSize = 10.sp, letterSpacing = 2.sp)
            }
            Text("● OFFLINE", color = Amber, fontSize = 11.sp, fontWeight = FontWeight.Bold)
        }

        Card(
            colors = CardDefaults.cardColors(containerColor = Panel),
            shape = RoundedCornerShape(20.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("ACCOUNT CONNECTION", color = Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                Text("Exness · MetaTrader 5", color = Color.White, fontSize = 19.sp, fontWeight = FontWeight.Bold)
                Text(
                    "Not connected. No broker credentials are stored in this app.",
                    color = Amber,
                    fontSize = 12.sp
                )
                Spacer(Modifier.height(2.dp))
                Text("Live prices and order execution are not enabled.", color = Muted, fontSize = 12.sp)
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            MetricCard("MARKET DATA", "Unavailable", "No live feed", Modifier.weight(1f))
            MetricCard("RISK GUARD", "Armed", "No orders sent", Modifier.weight(1f))
        }

        Card(
            colors = CardDefaults.cardColors(containerColor = Panel),
            shape = RoundedCornerShape(20.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("INTELLIGENCE LAB", color = Color.White, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                        Text("Autonomous execution remains locked until a real broker bridge is implemented.", color = Muted, fontSize = 12.sp)
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(if (botEnabled) "ARMED (UI ONLY)" else "BOT DISABLED", color = if (botEnabled) Amber else Muted, fontSize = 12.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                    Button(
                        onClick = { botEnabled = !botEnabled },
                        colors = ButtonDefaults.buttonColors(containerColor = if (botEnabled) Color(0xFF5B3440) else Mint)
                    ) {
                        Text(if (botEnabled) "Disarm" else "Arm UI", color = if (botEnabled) Color.White else Navy)
                    }
                }
                Text(
                    "Safety notice: this switch changes the screen state only. It cannot place, modify, or close trades.",
                    color = Amber,
                    fontSize = 11.sp
                )
            }
        }

        Text("WORKSPACE", color = Muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            listOf("Market", "Chart", "Analysis", "Lab").forEach { tab ->
                Button(
                    onClick = { selectedTab = tab },
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (selectedTab == tab) Mint else Panel,
                        contentColor = if (selectedTab == tab) Navy else Color.White
                    ),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 4.dp, vertical = 8.dp)
                ) {
                    Text(tab, fontSize = 10.sp)
                }
            }
        }
        Text(
            "$selectedTab selected · Starter interface",
            color = Muted,
            fontSize = 12.sp
        )
        Spacer(Modifier.weight(1f))
        Text(
            "SHARPENTRY 0.1.0 · STARTER BUILD · NOT CONNECTED TO A BROKER",
            color = Muted,
            fontSize = 9.sp,
            letterSpacing = 0.5.sp
        )
    }
}

@Composable
private fun MetricCard(title: String, value: String, detail: String, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = Panel),
        shape = RoundedCornerShape(16.dp)
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Text(title, color = Muted, fontSize = 10.sp, fontWeight = FontWeight.Bold)
            Text(value, color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
            Text(detail, color = Mint, fontSize = 10.sp)
        }
    }
}
