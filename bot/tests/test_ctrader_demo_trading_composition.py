import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from elite_trade.ctrader_order_requests import MarketOrderSpec
from elite_trade.ctrader_trading_loop_bridge import compose_demo_trading_loop_runtime
from elite_trade.ctrader_transport import ConnectionState
from elite_trade.instrument import InstrumentMetadata
from elite_trade.order_ledger import OrderLedger
from elite_trade.position_ledger import PositionLedger


class DemoTradingLoopCompositionTests(unittest.TestCase):
    def test_composition_binds_one_shot_bridge_to_account_bound_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = OrderLedger(Path(directory) / "orders.sqlite3", account_id=77)
            positions = PositionLedger(Path(directory) / "positions.sqlite3", account_id=77)
            transport = SimpleNamespace(
                environment="demo",
                requested_account_id=77,
                authenticated_account_id=None,
                state=ConnectionState.DISCONNECTED,
                position_reconciliation_in_flight=False,
                send_demo_order_request=lambda request: None,
                set_reconcile_handler=lambda handler: setattr(transport, "on_reconcile", handler),
                request_positions_reconcile=lambda: None,
            )
            instrument = InstrumentMetadata(
                symbol="EURUSD", symbol_id=42, digits=5, pip_position=4,
                volume_step_cents=100, min_volume_cents=100, max_volume_cents=100000,
                base_asset_id=1, quote_asset_id=2, base_asset="EUR", quote_asset="USD",
                symbol_trading_mode="ENABLED", account_access_rights="FULL_ACCESS",
                catalog_enabled=True, enable_short_selling=True,
            )
            runtime, submitter = compose_demo_trading_loop_runtime(
                transport=transport,
                ledger=ledger,
                expected_account_id=77,
                execution_type_names={2: "ORDER_ACCEPTED", 3: "ORDER_FILLED"},
                now_epoch=lambda: 100,
                on_unmatched=lambda reason, event: None,
                position_ledger=positions,
                symbol_names={42: "EURUSD"},
                instruments=lambda: (instrument,),
                client_order_id_factory=lambda: "composed-order",
            )
            self.assertIs(submitter._adapter, runtime.adapter)
            self.assertFalse(submitter.authorize_order(
                MarketOrderSpec(
                    account_id=77, symbol_id=42, side="BUY", volume_units=100,
                    client_order_id="outside-loop", entry_reference_price=1.1,
                    stop_loss=1.09, take_profit=1.12,
                ),
                "EURUSD",
            ))
            self.assertTrue(callable(runtime.on_execution_event))
            self.assertTrue(callable(runtime.on_order_error))


if __name__ == "__main__":
    unittest.main()
