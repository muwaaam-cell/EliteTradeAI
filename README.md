# EliteTrade AI — Android source starter

This is source code for an early native Android demo prototype, not a compiled APK.

## Build in GitHub Actions from an Android phone
1. Create a new **public** repository on GitHub (public standard GitHub-hosted Actions runners are free).
2. Upload all files from this folder to the repository root, including hidden `.github/workflows/build-apk.yml`.
3. Open the repository's **Actions** tab and select **Build EliteTrade AI APK**.
4. Tap **Run workflow** (or push to `main` to trigger a build).
5. Open the completed run, find **Artifacts**, and download `EliteTradeAI-debug-apk`. Extract the downloaded artifact ZIP to get `app-debug.apk`.

Do not add billing or payment details. Do not publish API keys, passwords, or broker credentials in a public repository. The project contains no broker credentials.

## Prototype scope and safety
- Mobile terminal tabs: Market, Chart, Analysis, Trade, Account.
- Simulated example prices only. No live data connection.
- Paper orders only. No real order execution.
- Exness and MT5 are not connected.
- No deposits or withdrawals.
- Live automated trading is disabled.
- The prototype is not yet comprehensively tested; test only in demo mode.

## If the build fails
Open the failed workflow and inspect the build log. The source uses Android Gradle Plugin 8.6.1 and Java 17; GitHub Actions downloads required build tools. A successful build has not been verified yet.
