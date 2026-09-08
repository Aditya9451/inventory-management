"""
Tests for restocking order API endpoints (POST/GET /api/restock-orders)
and the demand-forecast fields the Restocking tab depends on.
"""
import mock_data
import pytest


@pytest.fixture
def restock_orders_isolation():
    """Snapshot the in-memory restock_orders list and restore it after the test.

    main.py mutates this module-level list in place (restock_orders.insert), and
    the FastAPI app is shared across the whole test session, so without this the
    order created by one test would leak into every later test.
    """
    snapshot = mock_data.restock_orders[:]
    yield
    mock_data.restock_orders[:] = snapshot


def _valid_payload():
    return {
        "budget": 50000,
        "items": [
            {
                "item_sku": "WDG-001",
                "item_name": "Industrial Widget Type A",
                "quantity": 100,
                "unit_price": 12.5,
            },
            {
                "item_sku": "MTR-304",
                "item_name": "Electric Motor 5HP",
                "quantity": 10,
                "unit_price": 289.0,
            },
        ],
    }


class TestRestockOrderEndpoints:
    """Test suite for the restocking order endpoints."""

    def test_get_restock_orders_empty_by_default(self, client, restock_orders_isolation):
        """The restock orders list starts empty (nothing persisted to disk)."""
        response = client.get("/api/restock-orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_restock_order_happy_path(self, client, restock_orders_isolation):
        """Submitting a valid restock order returns the server-generated order."""
        response = client.post("/api/restock-orders", json=_valid_payload())
        assert response.status_code == 200

        order = response.json()
        assert order["order_number"].startswith("RSO-")
        assert order["status"] == "Submitted"
        assert order["budget"] == 50000
        assert len(order["items"]) == 2
        assert "id" in order and order["id"]

    def test_create_restock_order_total_value_calculation(self, client, restock_orders_isolation):
        """total_value equals the sum of quantity * unit_price across line items."""
        payload = _valid_payload()
        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 200

        expected = sum(i["quantity"] * i["unit_price"] for i in payload["items"])
        assert abs(response.json()["total_value"] - expected) < 0.01

    def test_create_restock_order_lead_time_is_max_of_items(self, client, restock_orders_isolation):
        """Order lead time is the slowest line item's lead_time_days (from the forecast)."""
        forecasts = {f["item_sku"]: f["lead_time_days"] for f in client.get("/api/demand").json()}
        payload = _valid_payload()
        expected_lead = max(forecasts[i["item_sku"]] for i in payload["items"])

        order = client.post("/api/restock-orders", json=payload).json()
        assert order["lead_time_days"] == expected_lead

    def test_create_restock_order_expected_delivery_after_order_date(self, client, restock_orders_isolation):
        """expected_delivery is order_date pushed out by the lead time."""
        order = client.post("/api/restock-orders", json=_valid_payload()).json()
        assert order["expected_delivery"] > order["order_date"]

    def test_created_restock_order_appears_in_list_newest_first(self, client, restock_orders_isolation):
        """A submitted order shows up in GET /api/restock-orders at the top."""
        first = client.post("/api/restock-orders", json=_valid_payload()).json()
        second = client.post("/api/restock-orders", json=_valid_payload()).json()

        listing = client.get("/api/restock-orders").json()
        assert [o["id"] for o in listing[:2]] == [second["id"], first["id"]]

    def test_create_restock_order_rejects_empty_items(self, client, restock_orders_isolation):
        """An order with no line items is a 400."""
        response = client.post("/api/restock-orders", json={"budget": 100, "items": []})
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_create_restock_order_requires_budget(self, client, restock_orders_isolation):
        """Missing the required budget field is a 422 validation error."""
        response = client.post(
            "/api/restock-orders",
            json={"items": [{"item_sku": "X", "item_name": "Y", "quantity": 1, "unit_price": 1.0}]},
        )
        assert response.status_code == 422


class TestDemandForecastRestockFields:
    """The Restocking tab reads unit_cost and lead_time_days off /api/demand."""

    def test_demand_forecasts_expose_unit_cost_and_lead_time(self, client):
        response = client.get("/api/demand")
        assert response.status_code == 200

        data = response.json()
        assert len(data) > 0
        for forecast in data:
            assert isinstance(forecast["unit_cost"], (int, float))
            assert forecast["unit_cost"] > 0
            assert isinstance(forecast["lead_time_days"], int)
            assert forecast["lead_time_days"] > 0
