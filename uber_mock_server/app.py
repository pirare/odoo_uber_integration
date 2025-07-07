from fastapi import FastAPI, HTTPException, Depends, Form, Header, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import sqlite3
import hashlib
import secrets
import time
from datetime import datetime, timedelta
import json
import base64
import hmac
import httpx
import asyncio
import uuid
import os

# Initialize FastAPI app
app = FastAPI(title="Uber Eats Mock Server", version="1.0.0")
security = HTTPBearer()

# Simple database setup
DB_FILE = "uber_mock.db"


def init_db():
    """Initialize SQLite database with all required tables"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Clients table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            client_id TEXT PRIMARY KEY,
            client_secret TEXT NOT NULL
        )
    ''')

    # Tokens table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tokens (
            access_token TEXT PRIMARY KEY,
            client_id TEXT,
            grant_type TEXT,
            scope TEXT,
            expires_at TIMESTAMP
        )
    ''')

    # Stores table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stores (
            store_id TEXT PRIMARY KEY,
            client_id TEXT,
            name TEXT,
            status TEXT DEFAULT 'online',
            webhook_url TEXT
        )
    ''')

    # Orders table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            store_id TEXT,
            status TEXT DEFAULT 'pending',
            order_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Webhook events table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS webhook_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT,
            store_id TEXT,
            order_id TEXT,
            payload TEXT,
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0
        )
    ''')

    # Store integrations table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS store_integrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT NOT NULL,
            client_id TEXT NOT NULL,
            integrator_store_id TEXT,
            integrator_brand_id TEXT,
            merchant_store_id TEXT,
            is_order_manager BOOLEAN DEFAULT TRUE,
            require_manual_acceptance BOOLEAN DEFAULT FALSE,
            integration_enabled BOOLEAN DEFAULT TRUE,
            store_configuration_data TEXT,
            webhooks_config TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(store_id, client_id)
        )
    ''')

    # Insert demo data
    cursor.execute('INSERT OR IGNORE INTO clients VALUES (?, ?)',
                   ('demo_client_id', 'demo_client_secret'))
    cursor.execute('INSERT OR IGNORE INTO stores VALUES (?, ?, ?, ?, ?)',
                   ('store_123', 'demo_client_id', 'Demo Restaurant', 'online', None))

    conn.commit()
    conn.close()


# Pydantic models
class TokenResponse(BaseModel):
    access_token: str
    expires_in: int
    token_type: str
    scope: str
    refresh_token: str = ""


class WebhookConfig(BaseModel):
    webhook_url: str


class MockOrder(BaseModel):
    store_id: str = "store_123"
    customer_name: str = "Test Customer"
    total: float = 25.99


class OrderAction(BaseModel):
    order_id: str
    reason: Optional[str] = None


# Helper functions
def generate_token():
    payload = {
        "id": f"mock_{secrets.token_hex(8)}",
        "expires_at": int(time.time()) + 2592000
    }
    payload_b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    signature = secrets.token_hex(16)
    return f"KA.{payload_b64}.{signature}"


def get_db():
    return sqlite3.connect(DB_FILE)


def validate_token(token: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT client_id, scope FROM tokens 
            WHERE access_token = ? AND expires_at > datetime('now')
        ''', (token,))
        result = cursor.fetchone()
        return {"client_id": result[0], "scope": result[1]} if result else None


def create_webhook_signature(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


async def send_webhook_async(webhook_url: str, payload: dict, secret: str):
    """Send webhook with basic retry logic"""
    payload_str = json.dumps(payload)
    headers = {
        'Content-Type': 'application/json',
        'X-Uber-Signature': create_webhook_signature(payload_str, secret)
    }

    for attempt in range(3):  # Simple 3-attempt retry
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(webhook_url, json=payload, headers=headers)
                if response.status_code == 200:
                    print(f"Webhook delivered to {webhook_url}")
                    return
                else:
                    print(f"Webhook failed: {response.status_code}")
        except Exception as e:
            print(f"Webhook attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)  # 0, 2, 4 seconds


# Auth dependency
async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token_info = validate_token(credentials.credentials)
    if not token_info:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token_info


# === AUTH ENDPOINTS ===
@app.post("/oauth/v2/token", response_model=TokenResponse)
async def get_token(
        client_id: str = Form(...),
        client_secret: str = Form(...),
        grant_type: str = Form(...),
        scope: str = Form(...)
):
    # Validate client
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT client_id FROM clients WHERE client_id = ? AND client_secret = ?',
                       (client_id, client_secret))
        if not cursor.fetchone():
            raise HTTPException(status_code=401, detail="Invalid client credentials")

    # Generate token
    access_token = generate_token()
    expires_at = datetime.now() + timedelta(days=30)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO tokens (access_token, client_id, grant_type, scope, expires_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (access_token, client_id, grant_type, scope, expires_at))
        conn.commit()

    return TokenResponse(
        access_token=access_token,
        expires_in=2592000,
        token_type="Bearer",
        scope=scope
    )


@app.get("/oauth/v2/authorize")
async def authorize(client_id: str, response_type: str, redirect_uri: str, scope: str):
    auth_code = secrets.token_urlsafe(32)
    return {
        "authorization_code": auth_code,
        "redirect_uri": redirect_uri,
        "note": "Use this code to exchange for access token"
    }


# === WEBHOOK ENDPOINTS ===
@app.post("/webhooks/configure")
async def configure_webhook(config: WebhookConfig, token_info=Depends(verify_token)):
    client_id = token_info['client_id']
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE stores SET webhook_url = ? WHERE client_id = ?',
                       (config.webhook_url, client_id))
        conn.commit()
    return {"message": "Webhook configured", "url": config.webhook_url}


# === STORE ENDPOINTS ===
@app.get("/v1/eats/stores")
async def get_stores(token_info=Depends(verify_token)):
    client_id = token_info['client_id']
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT store_id, name, status FROM stores WHERE client_id = ?', (client_id,))
        stores = cursor.fetchall()

    return {
        "stores": [
            {"id": store[0], "name": store[1], "status": store[2]}
            for store in stores
        ]
    }


@app.get("/v1/eats/stores/{store_id}")
async def get_store(store_id: str, token_info=Depends(verify_token)):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT store_id, name, status FROM stores WHERE store_id = ?', (store_id,))
        store = cursor.fetchone()

    if not store:
        raise HTTPException(status_code=404, detail="Store not found")

    return {"id": store[0], "name": store[1], "status": store[2]}


@app.post("/v1/eats/stores/{store_id}/status")
async def set_store_status(store_id: str, status: str, token_info=Depends(verify_token)):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE stores SET status = ? WHERE store_id = ?', (status, store_id))
        conn.commit()
    return {"message": f"Store {store_id} status set to {status}"}


# === INTEGRATION ACTIVATION & CONFIGURATION ENDPOINTS ===
@app.post("/v1/eats/stores/{store_id}/pos_data")
async def activate_integration(
        store_id: str,
        integration_data: dict,
        token_info=Depends(verify_token)
):
    """Associate an application with a location on Uber (requires eats.pos_provisioning scope)"""

    # Check scope - this endpoint requires eats.pos_provisioning
    if "eats.pos_provisioning" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.pos_provisioning scope"}
        )

    # Validate store exists
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT store_id FROM stores WHERE store_id = ?', (store_id,))
        if not cursor.fetchone():
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Store not found"}
            )

        # Store integration configuration
        cursor.execute('''
            INSERT OR REPLACE INTO store_integrations 
            (store_id, client_id, integrator_store_id, integrator_brand_id, 
             merchant_store_id, is_order_manager, require_manual_acceptance, 
             integration_enabled, store_configuration_data, webhooks_config)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            store_id,
            token_info['client_id'],
            integration_data.get('integrator_store_id'),
            integration_data.get('integrator_brand_id'),
            integration_data.get('merchant_store_id'),
            integration_data.get('is_order_manager', True),
            integration_data.get('require_manual_acceptance', False),
            True,  # Default enabled when activating
            integration_data.get('store_configuration_data'),
            json.dumps(integration_data.get('webhooks_config', {}))
        ))
        conn.commit()

    # Trigger store.provisioned webhook
    background_tasks.add_task(trigger_webhook, "store.provisioned", store_id)

    return {"message": "Integration activated successfully"}


@app.patch("/v1/eats/stores/{store_id}/pos_data")
async def update_integration(
        store_id: str,
        integration_data: dict,
        token_info=Depends(verify_token)
):
    """Update an integration's configuration (requires eats.store scope)"""

    # Check scope
    if "eats.store" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.store scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()

        # Check if integration exists for this client
        cursor.execute('''
            SELECT store_id FROM store_integrations 
            WHERE store_id = ? AND client_id = ?
        ''', (store_id, token_info['client_id']))

        if not cursor.fetchone():
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Integration not found"}
            )

        # Update integration configuration
        update_fields = []
        update_values = []

        if 'integrator_store_id' in integration_data:
            update_fields.append('integrator_store_id = ?')
            update_values.append(integration_data['integrator_store_id'])

        if 'integrator_brand_id' in integration_data:
            update_fields.append('integrator_brand_id = ?')
            update_values.append(integration_data['integrator_brand_id'])

        if 'merchant_store_id' in integration_data:
            update_fields.append('merchant_store_id = ?')
            update_values.append(integration_data['merchant_store_id'])

        if 'is_order_manager' in integration_data:
            update_fields.append('is_order_manager = ?')
            update_values.append(integration_data['is_order_manager'])

        if 'require_manual_acceptance' in integration_data:
            update_fields.append('require_manual_acceptance = ?')
            update_values.append(integration_data['require_manual_acceptance'])

        if 'integration_enabled' in integration_data:
            update_fields.append('integration_enabled = ?')
            update_values.append(integration_data['integration_enabled'])

        if 'store_configuration_data' in integration_data:
            update_fields.append('store_configuration_data = ?')
            update_values.append(integration_data['store_configuration_data'])

        if 'webhooks_config' in integration_data:
            update_fields.append('webhooks_config = ?')
            update_values.append(json.dumps(integration_data['webhooks_config']))

        if update_fields:
            update_values.extend([store_id, token_info['client_id']])
            cursor.execute(f'''
                UPDATE store_integrations 
                SET {', '.join(update_fields)}
                WHERE store_id = ? AND client_id = ?
            ''', update_values)
            conn.commit()

    return {"message": "Integration updated successfully"}


@app.get("/v1/eats/stores/{store_id}/pos_data")
async def get_integration(
        store_id: str,
        token_info=Depends(verify_token)
):
    """Retrieve a location's integration configuration (requires eats.store scope)"""

    # Check scope
    if "eats.store" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.store scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT integrator_store_id, integrator_brand_id, merchant_store_id,
                   is_order_manager, require_manual_acceptance, integration_enabled,
                   store_configuration_data, webhooks_config
            FROM store_integrations 
            WHERE store_id = ? AND client_id = ?
        ''', (store_id, token_info['client_id']))

        result = cursor.fetchone()

        if not result:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Integration not found"}
            )

        webhooks_config = json.loads(result[7]) if result[7] else {}

        return {
            "integrator_store_id": result[0],
            "integrator_brand_id": result[1],
            "merchant_store_id": result[2],
            "is_order_manager": result[3],
            "require_manual_acceptance": result[4],
            "integration_enabled": result[5],
            "store_configuration_data": result[6],
            "webhooks_config": webhooks_config,
            "allowed_customer_requests": {
                "allow_single_use_items_requests": False,
                "allow_special_instruction_requests": False
            }
        }


@app.delete("/v1/eats/stores/{store_id}/pos_data")
async def remove_integration(
        store_id: str,
        background_tasks: BackgroundTasks,
        token_info=Depends(verify_token)
):
    """Remove an application from a location (requires eats.pos_provisioning scope)"""

    # Check scope
    if "eats.pos_provisioning" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.pos_provisioning scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()

        # Check if integration exists
        cursor.execute('''
            SELECT store_id FROM store_integrations 
            WHERE store_id = ? AND client_id = ?
        ''', (store_id, token_info['client_id']))

        if not cursor.fetchone():
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Integration not found"}
            )

        # Remove integration
        cursor.execute('''
            DELETE FROM store_integrations 
            WHERE store_id = ? AND client_id = ?
        ''', (store_id, token_info['client_id']))
        conn.commit()

    # Trigger store.deprovisioned webhook
    background_tasks.add_task(trigger_webhook, "store.deprovisioned", store_id)

    return {"message": "Integration removed successfully"}


@app.get("/v1/eats/orders")
async def get_orders(token_info=Depends(verify_token)):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT o.order_id, o.store_id, o.status, o.order_data 
            FROM orders o 
            JOIN stores s ON o.store_id = s.store_id 
            WHERE s.client_id = ?
        ''', (token_info['client_id'],))
        orders = cursor.fetchall()

    return {
        "orders": [
            {
                "id": order[0],
                "store_id": order[1],
                "status": order[2],
                "data": json.loads(order[3]) if order[3] else {}
            }
            for order in orders
        ]
    }


@app.get("/v1/eats/orders/{order_id}")
async def get_order(order_id: str, token_info=Depends(verify_token)):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT order_data FROM orders WHERE order_id = ?', (order_id,))
        result = cursor.fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Order not found")

    return json.loads(result[0]) if result[0] else {}


@app.post("/v1/eats/orders/{order_id}/accept_pos_order")
async def accept_order(order_id: str, token_info=Depends(verify_token)):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE orders SET status = ? WHERE order_id = ?', ('accepted', order_id))
        conn.commit()
    return {"message": "Order accepted", "order_id": order_id}


@app.post("/v1/eats/orders/{order_id}/deny_pos_order")
async def deny_order(order_id: str, reason: str = Form("Unable to fulfill"), token_info=Depends(verify_token)):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE orders SET status = ? WHERE order_id = ?', ('denied', order_id))
        conn.commit()
    return {"message": "Order denied", "order_id": order_id, "reason": reason}


# === MENU ENDPOINTS ===
@app.get("/v1/eats/stores/{store_id}/menus")
async def get_menu(store_id: str, token_info=Depends(verify_token)):
    return {
        "menus": [
            {
                "id": "menu_1",
                "title": "Main Menu",
                "categories": [
                    {
                        "id": "cat_1",
                        "title": "Burgers",
                        "items": [
                            {"id": "item_1", "title": "Classic Burger", "price": 12.99},
                            {"id": "item_2", "title": "Cheese Burger", "price": 14.99}
                        ]
                    }
                ]
            }
        ]
    }


@app.put("/v1/eats/stores/{store_id}/menus")
async def upload_menu(store_id: str, menu_data: dict, token_info=Depends(verify_token)):
    return {"message": "Menu uploaded successfully", "store_id": store_id}


# === SIMULATION ENDPOINTS ===
@app.post("/simulate/order")
async def simulate_order(order: MockOrder, background_tasks: BackgroundTasks):
    order_id = f"order_{uuid.uuid4().hex[:8]}"

    # Create order
    order_data = {
        "id": order_id,
        "store_id": order.store_id,
        "customer": {"name": order.customer_name},
        "total": order.total,
        "status": "pending",
        "created_at": datetime.now().isoformat()
    }

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO orders (order_id, store_id, order_data) 
            VALUES (?, ?, ?)
        ''', (order_id, order.store_id, json.dumps(order_data)))

        # Get webhook URL
        cursor.execute('SELECT webhook_url FROM stores WHERE store_id = ?', (order.store_id,))
        result = cursor.fetchone()
        webhook_url = result[0] if result else None
        conn.commit()

    # Send webhook if configured
    if webhook_url:
        webhook_payload = {
            "event_id": f"event_{uuid.uuid4().hex[:8]}",
            "event_type": "orders.notification",
            "event_time": int(time.time()),
            "resource_id": order_id,
            "user_id": order.store_id,
            "meta": {"status": "created"}
        }
        background_tasks.add_task(send_webhook_async, webhook_url, webhook_payload, "demo_client_secret")

    return {"message": "Order created", "order_id": order_id, "webhook_sent": webhook_url is not None}


@app.post("/simulate/order/cancel/{order_id}")
async def simulate_cancel(order_id: str, background_tasks: BackgroundTasks):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE orders SET status = ? WHERE order_id = ?', ('cancelled', order_id))
        cursor.execute('SELECT store_id FROM orders WHERE order_id = ?', (order_id,))
        result = cursor.fetchone()
        store_id = result[0] if result else None

        if store_id:
            cursor.execute('SELECT webhook_url FROM stores WHERE store_id = ?', (store_id,))
            result = cursor.fetchone()
            webhook_url = result[0] if result else None
        conn.commit()

    # Send cancel webhook
    if webhook_url:
        webhook_payload = {
            "event_id": f"event_{uuid.uuid4().hex[:8]}",
            "event_type": "orders.cancel",
            "event_time": int(time.time()),
            "resource_id": order_id,
            "user_id": store_id
        }
        background_tasks.add_task(send_webhook_async, webhook_url, webhook_payload, "demo_client_secret")

    return {"message": "Order cancelled", "order_id": order_id}


# === ROOT & HEALTH ===
@app.get("/")
async def root():
    return {
        "name": "Uber Eats Mock Server",
        "endpoints": {
            "auth": "/oauth/v2/token",
            "stores": "/v1/eats/stores",
            "orders": "/v1/eats/orders",
            "pos_data": "/v1/eats/stores/{store_id}/pos_data",
            "simulate": "/simulate/order"
        },
        "demo": {
            "client_id": "demo_client_id",
            "client_secret": "demo_client_secret"
        }
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


# Initialize on startup
@app.on_event("startup")
async def startup():
    init_db()
    print("Uber Eats Mock Server started!")
    print("Docs: http://localhost:8000/docs")
    print("Demo credentials: demo_client_id / demo_client_secret")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)