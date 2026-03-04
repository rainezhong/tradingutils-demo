import unittest
from unittest.mock import MagicMock
from signal_extraction.execution.order_manager import OrderManager, OrderSide


class TestOrderManagerFix(unittest.TestCase):
    def test_submit_sell_order_yes_side(self):
        # Mock client
        mock_client = MagicMock()

        # Initialize OrderManager (not dry run to test API call)
        om = OrderManager(mock_client, dry_run=False)

        # Create a sell order on the "yes" side
        ticker = "TEST_TICKER"
        price = 0.38
        quantity = 5
        order = om.create_order(
            ticker=ticker,
            side=OrderSide.SELL,
            quantity=quantity,
            price=price,
            market_side="yes",
        )

        # Submit the order
        om.submit_order(order)

        # Verify mock_client.create_order was called with correct arguments
        # Action should be "sell", side should be "yes", and yes_price should be 38
        mock_client.create_order.assert_called_with(
            ticker=ticker,
            action="sell",
            side="yes",
            count=quantity,
            type="limit",
            yes_price=38,
            no_price=None,
        )
        print("✓ Sell order on 'yes' side correctly used 'yes_price'")

    def test_submit_buy_order_yes_side(self):
        mock_client = MagicMock()
        om = OrderManager(mock_client, dry_run=False)

        ticker = "TEST_TICKER"
        price = 0.38
        quantity = 5
        order = om.create_order(
            ticker=ticker,
            side=OrderSide.BUY,
            quantity=quantity,
            price=price,
            market_side="yes",
        )

        om.submit_order(order)

        mock_client.create_order.assert_called_with(
            ticker=ticker,
            action="buy",
            side="yes",
            count=quantity,
            type="limit",
            yes_price=38,
            no_price=None,
        )
        print("✓ Buy order on 'yes' side correctly used 'yes_price'")


if __name__ == "__main__":
    unittest.main()
