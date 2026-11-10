"""Read-only cTrader demo account authentication runtime.

No strategies, order submission, or live-account support are enabled here.
"""
import signal
import threading

from dotenv import load_dotenv
from twisted.internet import reactor

from elite_trade.config import Settings
from elite_trade.ctrader_transport import CTraderSdkTransport, ConnectionState
from elite_trade.token_vault import TokenVault


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    if settings.ctrader_env != "demo":
        raise SystemExit("STOPPED: This read-only runtime accepts DEMO mode only.")

    if not settings.ctrader_client_id or not settings.ctrader_client_secret:
        raise SystemExit("STOPPED: cTrader application credentials are not configured.")

    # Load the existing encrypted token. Never print credentials or tokens.
    try:
        token_data = TokenVault().load()
    except Exception as exc:
        raise SystemExit(
            f"STOPPED: Could not load encrypted cTrader token ({type(exc).__name__})."
        )

    if not isinstance(token_data, dict) or not token_data.get("access_token"):
        raise SystemExit("STOPPED: The token vault has no usable access token.")

    access_token = token_data["access_token"]

    transport = None
    shutting_down = False
    selection_started = False

    def shutdown():
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        print("\nStopping read-only cTrader connection...")
        try:
            if transport is not None:
                transport.stop()
        finally:
            if reactor.running:
                reactor.stop()

    def request_shutdown():
        if reactor.running:
            reactor.callFromThread(shutdown)
        else:
            print("Shutdown requested before reactor startup.")

    def on_state(state):
        print(f"Connection state: {state.name}")
        if state == ConnectionState.AUTHENTICATED:
            print("DEMO ACCOUNT AUTHENTICATED.")
            print("Read-only session. No orders can be submitted by this runtime.")

    def on_error(message):
        print(f"cTrader connection stopped: {message}")
        request_shutdown()

    def on_accounts(accounts):
        nonlocal selection_started
        if selection_started:
            return
        selection_started = True

        print("\nAccounts returned by cTrader:")
        demo_ids = set()
        for account in accounts:
            label = "DEMO" if not account.is_live else "LIVE"
            print(f"  Account ID: {account.account_id} | Type: {label}")
            if not account.is_live:
                demo_ids.add(account.account_id)

        if not demo_ids:
            print("No demo account was returned. No account selected.")
            request_shutdown()
            return

        def prompt_for_account():
            try:
                answer = input("\nEnter the DEMO account ID to authenticate: ").strip()
                selected_id = int(answer)
                if selected_id not in demo_ids:
                    print("That ID is not one of the discovered demo accounts.")
                    request_shutdown()
                    return
                reactor.callFromThread(select_account, selected_id)
            except (ValueError, EOFError):
                print("Invalid selection or input unavailable. No account selected.")
                request_shutdown()

        threading.Thread(
            target=prompt_for_account,
            name="ctrader-demo-account-selection",
            daemon=True,
        ).start()

    def select_account(account_id):
        try:
            transport.select_account(account_id)
        except Exception as exc:
            print(f"Account selection failed: {type(exc).__name__}")
            shutdown()

    transport = CTraderSdkTransport(
        environment="demo",
        client_id=settings.ctrader_client_id,
        client_secret=settings.ctrader_client_secret,
        access_token=access_token,
        account_id=None,
        on_accounts=on_accounts,
        on_state=on_state,
        on_error=on_error,
        reactor=reactor,
    )

    def handle_signal(signum, frame):
        request_shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("EliteTrade AI — cTrader read-only DEMO connection")
    print("Trading: DISABLED")
    print("Live accounts: NOT SELECTABLE")
    print("Starting connection...")

    try:
        transport.start()
        reactor.run(installSignalHandlers=False)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as exc:
        print(f"Runtime stopped: {type(exc).__name__}: {exc}")
    finally:
        if not shutting_down:
            try:
                transport.stop()
            except Exception:
                pass
        if reactor.running:
            reactor.stop()
        print("Read-only runtime stopped. No orders were submitted.")


if __name__ == "__main__":
    main()
