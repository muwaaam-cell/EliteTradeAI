package com.example.sharpentry

import android.app.Activity
import android.os.Bundle
import android.view.Gravity
import android.widget.*
import kotlin.math.abs
import kotlin.math.round

class MainActivity : Activity() {
    private val symbols = listOf("XAUUSD", "BTCUSD")
    private val history = mutableListOf<String>()
    private lateinit var status: TextView
    private lateinit var symbolSpinner: Spinner
    private lateinit var priceInput: EditText
    private lateinit var stopInput: EditText
    private lateinit var balanceInput: EditText
    private lateinit var riskInput: EditText
    private var paused = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 24, 24, 24)
        }
        fun label(text: String) {
            root.addView(TextView(this).apply {
                this.text = text
                textSize = 16f
                setPadding(0, 12, 0, 4)
            })
        }
        fun input(hintText: String): EditText = EditText(this).also {
            it.hint = hintText
            it.inputType = android.text.InputType.TYPE_CLASS_NUMBER or
                android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL or
                android.text.InputType.TYPE_NUMBER_FLAG_SIGNED
            root.addView(it)
        }
        root.addView(TextView(this).apply {
            text = "Sharp Entry — Paper Trading"
            textSize = 22f
            gravity = Gravity.CENTER
        })
        root.addView(TextView(this).apply {
            text = "Signals are educational, not financial advice. No live orders."
            textSize = 13f
            setPadding(0, 8, 0, 8)
        })
        label("Instrument")
        symbolSpinner = Spinner(this)
        symbolSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, symbols)
        root.addView(symbolSpinner)
        label("Current price")
        priceInput = input("Enter current price")
        label("Stop-loss price")
        stopInput = input("Enter stop-loss price")
        label("Account balance (USD)")
        balanceInput = input("Example: 1000")
        label("Risk per trade (%)")
        riskInput = input("Example: 1")
        riskInput.setText("1")
        status = TextView(this).apply {
            text = "No signal. Enter prices to check the rules."
            textSize = 16f
            setPadding(0, 16, 0, 16)
        }
        root.addView(status)
        root.addView(Button(this).apply {
            text = "Check selective entry rules"
            setOnClickListener { checkSignal() }
        })
        root.addView(Button(this).apply {
            text = "Pause signals"
            setOnClickListener {
                paused = !paused
                text = if (paused) "Resume signals" else "Pause signals"
                status.text = if (paused) "Paused. No signals will be checked."
                    else "Resumed. Enter prices to check the rules."
            }
        })
        root.addView(Button(this).apply {
            text = "Clear fields"
            setOnClickListener {
                priceInput.text.clear()
                stopInput.text.clear()
                balanceInput.text.clear()
                riskInput.setText("1")
                status.text = "No signal. Enter prices to check the rules."
            }
        })
        label("Paper trade history")
        root.addView(Button(this).apply {
            text = "Show history"
            setOnClickListener {
                status.text = if (history.isEmpty()) "No paper entries recorded."
                    else history.takeLast(10).reversed().joinToString("\n")
            }
        })
        setContentView(ScrollView(this).apply { addView(root) })
    }

    private fun checkSignal() {
        if (paused) {
            status.text = "Paused. Resume before checking."
            return
        }
        val symbol = symbolSpinner.selectedItem?.toString() ?: return
        val price = priceInput.text.toString().toDoubleOrNull()
        val stop = stopInput.text.toString().toDoubleOrNull()
        val balance = balanceInput.text.toString().toDoubleOrNull()
        val riskPercent = riskInput.text.toString().toDoubleOrNull()
        if (price == null || stop == null || balance == null || riskPercent == null ||
            price <= 0 || stop <= 0 || balance <= 0 || riskPercent <= 0 || riskPercent > 2
        ) {
            status.text = "Enter valid positive values. Risk must be over 0% and at most 2%."
            return
        }
        val stopDistance = abs(price - stop)
        if (stopDistance == 0.0) {
            status.text = "Stop-loss must differ from the entry price."
            return
        }
        val isLong = stop < price
        val riskAmount = balance * riskPercent / 100.0
        val units = riskAmount / stopDistance
        // Simplified paper-trading math only; not calibrated to broker contract sizes.
        val direction = if (isLong) "PAPER BUY" else "PAPER SELL"
        val message = "$direction $symbol\nEntry: $price | Stop: $stop\n" +
            "Risk budget: \$${format(riskAmount)} | Approx. units: ${format(units)}\n" +
            "Check trend, spread, and news manually. Signal is not a guarantee."
        status.text = message
        history.add(message)
    }

    private fun format(value: Double): String =
        round(value * 100.0).toLong().div(100.0).toString()
}
