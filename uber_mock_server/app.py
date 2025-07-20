from fastapi import FastAPI, HTTPException, Depends, Form, Header, BackgroundTasks, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import RedirectResponse, HTMLResponse
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
import urllib.parse
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
import logging
import sys


def setup_logging():
    """Configure logging for the application"""

    # Create logs directory
    import os
    os.makedirs("logs", exist_ok=True)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        'logs/app.log',
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(formatter)

    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

logger = setup_logging()


# Initialize FastAPI app
app = FastAPI(title="Uber Eats Mock Server", version="2.0.0")
security = HTTPBearer()

# Configuration
DB_FILE = os.environ.get("UBER_MOCK_DB", "uber_mock.db")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "demo_webhook_secret")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests"""
    start_time = time.time()

    # Log request
    logger.info(f"Request: {request.method} {request.url}")

    # Process request
    response = await call_next(request)

    # Log response
    process_time = time.time() - start_time
    logger.info(f"Response: {response.status_code} - {process_time:.3f}s")

    return response


# Database connection manager
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize SQLite database with all required tables"""
    with get_db() as conn:
        cursor = conn.cursor()

        # Clients table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                client_id TEXT PRIMARY KEY,
                client_secret TEXT NOT NULL,
                name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Authorization codes table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auth_codes (
                code TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                scope TEXT NOT NULL,
                state TEXT,
                expires_at TIMESTAMP NOT NULL,
                used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Tokens table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tokens (
                access_token TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                grant_type TEXT NOT NULL,
                scope TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (client_id) REFERENCES clients(client_id)
            )
        ''')

        # Stores table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stores (
                store_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                name TEXT NOT NULL,
                external_store_id TEXT,
                status TEXT DEFAULT 'online',
                is_paused BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (client_id) REFERENCES clients(client_id)
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
                webhook_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(store_id, client_id),
                FOREIGN KEY (store_id) REFERENCES stores(store_id),
                FOREIGN KEY (client_id) REFERENCES clients(client_id)
            )
        ''')

        # Orders table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                store_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                order_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (store_id) REFERENCES stores(store_id)
            )
        ''')

        # Webhook events table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS webhook_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                store_id TEXT NOT NULL,
                order_id TEXT,
                payload TEXT NOT NULL,
                webhook_url TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                attempts INTEGER DEFAULT 0,
                last_attempt_at TIMESTAMP,
                next_retry_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (store_id) REFERENCES stores(store_id)
            )
        ''')

        # Insert demo data
        cursor.execute('''
            INSERT OR IGNORE INTO clients (client_id, client_secret, name) 
            VALUES (?, ?, ?)
        ''', ('demo_client_id', 'demo_client_secret', 'Demo Application'))

        cursor.execute('''
            INSERT OR IGNORE INTO stores (store_id, client_id, name, external_store_id) 
            VALUES (?, ?, ?, ?)
        ''', ('store_123', 'demo_client_id', 'Demo Restaurant', 'EXT123'))

        cursor.execute('''
            INSERT OR IGNORE INTO stores (store_id, client_id, name, external_store_id) 
            VALUES (?, ?, ?, ?)
        ''', ('store_456', 'demo_client_id', 'Demo Cafe', 'EXT456'))

        conn.commit()


# Pydantic models
class TokenResponse(BaseModel):
    access_token: str
    expires_in: int
    token_type: str
    scope: str
    refresh_token: str = ""


class StoreStatusUpdate(BaseModel):
    paused: bool


class IntegrationConfig(BaseModel):
    integrator_store_id: Optional[str] = None
    integrator_brand_id: Optional[str] = None
    merchant_store_id: Optional[str] = None
    is_order_manager: Optional[bool] = True
    require_manual_acceptance: Optional[bool] = False
    integration_enabled: Optional[bool] = True
    store_configuration_data: Optional[str] = None
    allowed_customer_requests: Optional[Dict[str, bool]] = None
    webhooks_config: Optional[Dict[str, Any]] = None


class MockOrder(BaseModel):
    store_id: str
    customer_name: str = "Test Customer"
    total: float = 25.99
    items: Optional[List[Dict[str, Any]]] = None


class OrderAction(BaseModel):
    reason: Optional[str] = None


# Helper functions
def generate_token():
    """Generate a realistic Uber-style token"""
    payload = {
        "version": 2,
        "id": f"mock_{secrets.token_hex(8)}",
        "expires_at": int(time.time()) + 2592000,
        "pipeline_key_id": "mock",
        "pipeline_id": 1
    }
    payload_b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    signature = secrets.token_hex(32)
    return f"KA.{payload_b64}.{signature}"


def validate_token(token: str) -> Optional[Dict[str, str]]:
    """Validate token and return token info"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT client_id, scope, grant_type 
            FROM tokens 
            WHERE access_token = ? AND expires_at > datetime('now')
        ''', (token,))
        result = cursor.fetchone()

        if result:
            return {
                "client_id": result["client_id"],
                "scope": result["scope"],
                "grant_type": result["grant_type"]
            }
        return None


def create_webhook_signature(payload: str, secret: str) -> str:
    """Create HMAC-SHA256 signature for webhook"""
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


async def send_webhook_with_retry(event_id: str, webhook_url: str, payload: dict, attempt: int = 1):
    """Send webhook with exponential backoff retry"""
    max_attempts = 3

    try:
        payload_str = json.dumps(payload)
        headers = {
            'Content-Type': 'application/json',
            'X-Uber-Signature': create_webhook_signature(payload_str, WEBHOOK_SECRET)
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook_url, json=payload, headers=headers)

            if response.status_code == 200:
                # Mark as delivered
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE webhook_events 
                        SET status = 'delivered', attempts = ?
                        WHERE event_id = ?
                    ''', (attempt, event_id))
                    conn.commit()
                print(f"Webhook {event_id} delivered successfully")
                return
            else:
                raise Exception(f"HTTP {response.status_code}")

    except Exception as e:
        print(f"Webhook {event_id} attempt {attempt} failed: {e}")

        if attempt < max_attempts:
            # Calculate next retry time (exponential backoff)
            retry_delay = 2 ** attempt
            next_retry = datetime.now() + timedelta(seconds=retry_delay)

            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE webhook_events 
                    SET attempts = ?, last_attempt_at = datetime('now'), 
                        next_retry_at = ?, status = 'retrying'
                    WHERE event_id = ?
                ''', (attempt, next_retry, event_id))
                conn.commit()

            # Schedule retry
            await asyncio.sleep(retry_delay)
            await send_webhook_with_retry(event_id, webhook_url, payload, attempt + 1)
        else:
            # Mark as failed
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE webhook_events 
                    SET status = 'failed', attempts = ?
                    WHERE event_id = ?
                ''', (attempt, event_id))
                conn.commit()


async def trigger_webhook(event_type: str, store_id: str, order_id: str = None,
                          data: dict = None, delay: float = 1.0):
    """Trigger a webhook event with realistic delay"""
    # Add realistic delay
    await asyncio.sleep(delay)

    # Get webhook URL from integration
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT webhook_url, webhooks_config 
            FROM store_integrations 
            WHERE store_id = ? AND integration_enabled = 1
        ''', (store_id,))
        result = cursor.fetchone()

        if not result or not result['webhook_url']:
            print(f"No webhook URL configured for store {store_id}")
            return

        webhook_url = result['webhook_url']
        webhooks_config = json.loads(result['webhooks_config'] or '{}')

        # Check if this event type is enabled
        if event_type == 'orders.release' and not webhooks_config.get('order_release_webhooks', {}).get('is_enabled'):
            return
        if event_type == 'orders.scheduled.notification' and not webhooks_config.get('schedule_order_webhooks', {}).get(
                'is_enabled'):
            return
        if event_type == 'delivery.state_changed' and not webhooks_config.get('delivery_status_webhooks', {}).get(
                'is_enabled'):
            return

        # Create webhook payload
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        webhook_payload = {
            "event_id": event_id,
            "event_type": event_type,
            "event_time": int(time.time()),
            "resource_id": order_id or store_id,
            "resource_href": f"{BASE_URL}/v1/delivery/order/{order_id}" if order_id else None,
            "meta": {
                "user_id": store_id,
                "resource_id": order_id or store_id,
                "status": data.get('status') if data else None
            }
        }

        # Store webhook event
        cursor.execute('''
            INSERT INTO webhook_events 
            (event_id, event_type, store_id, order_id, payload, webhook_url)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (event_id, event_type, store_id, order_id, json.dumps(webhook_payload), webhook_url))
        conn.commit()

    # Send webhook asynchronously
    await send_webhook_with_retry(event_id, webhook_url, webhook_payload)


# Auth dependency
async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token_info = validate_token(credentials.credentials)
    if not token_info:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "Invalid OAuth 2.0 credentials provided"}
        )
    return token_info


# === AUTH ENDPOINTS ===
@app.get("/oauth/v2/authorize")
async def authorize(
        client_id: str,
        response_type: str,
        redirect_uri: str,
        scope: str,
        state: Optional[str] = None
):
    """OAuth authorization endpoint - returns HTML with auto-approval for testing"""

    # Validate client
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT client_id FROM clients WHERE client_id = ?', (client_id,))
        if not cursor.fetchone():
            return HTMLResponse(content="<h1>Invalid client_id</h1>", status_code=400)

    # Generate authorization code
    auth_code = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(minutes=10)

    # Store auth code
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO auth_codes (code, client_id, redirect_uri, scope, state, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (auth_code, client_id, redirect_uri, scope, state, expires_at))
        conn.commit()

    # Build redirect URL
    params = {"code": auth_code}
    if state:
        params["state"] = state

    redirect_url = f"{redirect_uri}?{urllib.parse.urlencode(params)}"

    # Return auto-approval page
    html_content = f"""
    <html>
    <head>
        <title>Uber Eats Authorization</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 50px; }}
            .container {{ max-width: 500px; margin: 0 auto; }}
            .btn {{ background: #000; color: white; padding: 10px 20px; 
                    text-decoration: none; display: inline-block; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Uber Eats Authorization</h1>
            <p>Application <strong>{client_id}</strong> is requesting access to:</p>
            <ul>
                <li>Scope: {scope}</li>
            </ul>
            <p>For testing, this will auto-approve in 2 seconds...</p>
            <a href="{redirect_url}" class="btn">Approve Now</a>
        </div>
        <script>
            setTimeout(function() {{
                window.location.href = "{redirect_url}";
            }}, 2000);
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@app.post("/oauth/v2/token", response_model=TokenResponse)
async def get_token(
        client_id: str = Form(...),
        client_secret: str = Form(...),
        grant_type: str = Form(...),
        scope: Optional[str] = Form(None),
        code: Optional[str] = Form(None),
        redirect_uri: Optional[str] = Form(None)
):
    """Token endpoint for both client_credentials and authorization_code flows"""

    # Validate client
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT client_id FROM clients WHERE client_id = ? AND client_secret = ?',
            (client_id, client_secret)
        )
        if not cursor.fetchone():
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_client", "error_description": "Invalid client credentials"}
            )

    if grant_type == "client_credentials":
        # Client credentials flow
        if not scope:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_request", "error_description": "Scope is required"}
            )

    elif grant_type == "authorization_code":
        # Authorization code flow
        if not code or not redirect_uri:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_request", "error_description": "Code and redirect_uri are required"}
            )

        # Validate auth code
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT scope FROM auth_codes 
                WHERE code = ? AND client_id = ? AND redirect_uri = ? 
                AND expires_at > datetime('now') AND used = 0
            ''', (code, client_id, redirect_uri))
            result = cursor.fetchone()

            if not result:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "invalid_grant", "error_description": "Invalid or expired authorization code"}
                )

            scope = result['scope']

            # Mark code as used
            cursor.execute('UPDATE auth_codes SET used = 1 WHERE code = ?', (code,))
            conn.commit()
    else:
        raise HTTPException(
            status_code=400,
            detail={"error": "unsupported_grant_type", "error_description": "Grant type not supported"}
        )

    # Generate token
    access_token = generate_token()
    expires_at = datetime.now() + timedelta(days=30)

    # Store token
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


# === STORE ENDPOINTS ===
@app.get("/v1/eats/stores")
async def get_stores(token_info=Depends(verify_token)):
    """Get stores - behavior depends on token type"""

    with get_db() as conn:
        cursor = conn.cursor()

        if token_info['grant_type'] == 'authorization_code':
            # For user tokens, return all stores (simulating merchant's stores)
            cursor.execute('''
                SELECT store_id, name, external_store_id, status 
                FROM stores
            ''')
        else:
            # For app tokens, return only integrated stores
            cursor.execute('''
                SELECT s.store_id, s.name, s.external_store_id, s.status 
                FROM stores s
                JOIN store_integrations si ON s.store_id = si.store_id
                WHERE si.client_id = ? AND si.integration_enabled = 1
            ''', (token_info['client_id'],))

        stores = cursor.fetchall()

    return {
        "stores": [
            {
                "store_id": store["store_id"],
                "name": store["name"],
                "external_store_id": store["external_store_id"],
                "status": store["status"]
            }
            for store in stores
        ]
    }


@app.get("/v1/eats/stores/{store_id}")
async def get_store(store_id: str, token_info=Depends(verify_token)):
    """Get store details"""

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT store_id, name, external_store_id, status, is_paused 
            FROM stores 
            WHERE store_id = ?
        ''', (store_id,))
        store = cursor.fetchone()

    if not store:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Store not found"}
        )

    return {
        "store_id": store["store_id"],
        "name": store["name"],
        "external_store_id": store["external_store_id"],
        "status": store["status"],
        "is_paused": bool(store["is_paused"])
    }


@app.patch("/v1/eats/stores/{store_id}/status")
async def update_store_status(
        store_id: str,
        status_update: StoreStatusUpdate,
        token_info=Depends(verify_token)
):
    """Update store status (pause/unpause)"""

    # Check scope
    if "eats.store.status.write" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.store.status.write scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE stores 
            SET is_paused = ?, status = ?
            WHERE store_id = ?
        ''', (status_update.paused, 'paused' if status_update.paused else 'online', store_id))

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Store not found"}
            )

        conn.commit()

    return {"message": f"Store {'paused' if status_update.paused else 'unpaused'} successfully"}


# === INTEGRATION ENDPOINTS ===
@app.post("/v1/eats/stores/{store_id}/pos_data")
async def activate_integration(
        store_id: str,
        integration_data: IntegrationConfig,
        background_tasks: BackgroundTasks,
        token_info=Depends(verify_token)
):
    """Activate integration - requires authorization_code token"""

    if "eats.pos_provisioning" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.pos_provisioning scope"}
        )

    # Verify store exists
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT store_id FROM stores WHERE store_id = ?', (store_id,))
        if not cursor.fetchone():
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Store not found"}
            )

        # Create or update integration
        webhooks_config = integration_data.webhooks_config or {
            "order_release_webhooks": {"is_enabled": False},
            "schedule_order_webhooks": {"is_enabled": True},
            "delivery_status_webhooks": {"is_enabled": True},
            "webhooks_version": "1.0.0"
        }

        # Extract webhook URL from Odoo's base URL
        # In real scenario, this would be configured differently
        webhook_url = f"http://localhost:8069/ubereats/webhook/{store_id}"

        cursor.execute('''
            INSERT OR REPLACE INTO store_integrations 
            (store_id, client_id, integrator_store_id, integrator_brand_id, 
             merchant_store_id, is_order_manager, require_manual_acceptance, 
             integration_enabled, store_configuration_data, webhooks_config, webhook_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            store_id,
            token_info['client_id'],
            integration_data.integrator_store_id,
            integration_data.integrator_brand_id,
            integration_data.merchant_store_id,
            integration_data.is_order_manager,
            integration_data.require_manual_acceptance,
            True,
            integration_data.store_configuration_data,
            json.dumps(webhooks_config),
            webhook_url
        ))
        conn.commit()

    # Trigger webhook
    background_tasks.add_task(
        trigger_webhook,
        "store.provisioned",
        store_id,
        data={"status": "activated"}
    )

    return {"message": "Integration activated successfully"}


@app.patch("/v1/eats/stores/{store_id}/pos_data")
async def update_integration(
        store_id: str,
        integration_data: Dict[str, Any],
        token_info=Depends(verify_token)
):
    """Update integration configuration"""

    if "eats.store" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.store scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()

        # Check integration exists
        cursor.execute('''
            SELECT id FROM store_integrations 
            WHERE store_id = ? AND client_id = ?
        ''', (store_id, token_info['client_id']))

        if not cursor.fetchone():
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Integration not found"}
            )

        # Build update query dynamically
        update_fields = []
        update_values = []

        field_mapping = {
            'integrator_store_id': 'integrator_store_id',
            'integrator_brand_id': 'integrator_brand_id',
            'merchant_store_id': 'merchant_store_id',
            'is_order_manager': 'is_order_manager',
            'require_manual_acceptance': 'require_manual_acceptance',
            'integration_enabled': 'integration_enabled',
            'store_configuration_data': 'store_configuration_data'
        }

        for api_field, db_field in field_mapping.items():
            if api_field in integration_data:
                update_fields.append(f'{db_field} = ?')
                update_values.append(integration_data[api_field])

        if 'webhooks_config' in integration_data:
            update_fields.append('webhooks_config = ?')
            update_values.append(json.dumps(integration_data['webhooks_config']))

        if update_fields:
            update_fields.append('updated_at = datetime("now")')
            update_values.extend([store_id, token_info['client_id']])

            query = f'''
                UPDATE store_integrations 
                SET {', '.join(update_fields)}
                WHERE store_id = ? AND client_id = ?
            '''
            cursor.execute(query, update_values)
            conn.commit()

    return {"message": "Integration updated successfully"}


@app.get("/v1/eats/stores/{store_id}/pos_data")
async def get_integration(store_id: str, token_info=Depends(verify_token)):
    """Get integration configuration"""

    if "eats.store" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.store scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM store_integrations 
            WHERE store_id = ? AND client_id = ?
        ''', (store_id, token_info['client_id']))

        result = cursor.fetchone()

        if not result:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Integration not found"}
            )

        webhooks_config = json.loads(result['webhooks_config'] or '{}')

        return {
            "integrator_store_id": result['integrator_store_id'],
            "integrator_brand_id": result['integrator_brand_id'],
            "merchant_store_id": result['merchant_store_id'],
            "is_order_manager": bool(result['is_order_manager']),
            "require_manual_acceptance": bool(result['require_manual_acceptance']),
            "integration_enabled": bool(result['integration_enabled']),
            "store_configuration_data": result['store_configuration_data'],
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
    """Remove integration"""

    if "eats.pos_provisioning" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.pos_provisioning scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            DELETE FROM store_integrations 
            WHERE store_id = ? AND client_id = ?
        ''', (store_id, token_info['client_id']))

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Integration not found"}
            )

        conn.commit()

    # Trigger webhook
    background_tasks.add_task(
        trigger_webhook,
        "store.deprovisioned",
        store_id,
        data={"status": "deactivated"}
    )

    return {"message": "Integration removed successfully"}


# === ORDER ENDPOINTS ===
@app.get("/v1/eats/orders")
async def get_orders(token_info=Depends(verify_token)):
    """Get orders for integrated stores"""

    if "eats.order" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.order scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT o.order_id, o.store_id, o.status, o.order_data, o.created_at
            FROM orders o
            JOIN store_integrations si ON o.store_id = si.store_id
            WHERE si.client_id = ? AND si.integration_enabled = 1
            ORDER BY o.created_at DESC
            LIMIT 100
        ''', (token_info['client_id'],))
        orders = cursor.fetchall()

    return {
        "orders": [
            {
                "order_id": order["order_id"],
                "store_id": order["store_id"],
                "status": order["status"],
                "data": json.loads(order["order_data"]),
                "created_at": order["created_at"]
            }
            for order in orders
        ]
    }


@app.get("/v1/eats/orders/{order_id}")
async def get_order(order_id: str, token_info=Depends(verify_token)):
    """Get order details"""

    if "eats.order" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.order scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT o.*, si.client_id
            FROM orders o
            JOIN store_integrations si ON o.store_id = si.store_id
            WHERE o.order_id = ? AND si.client_id = ?
        ''', (order_id, token_info['client_id']))
        result = cursor.fetchone()

    if not result:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Order not found"}
        )

    order_data = json.loads(result['order_data'])
    order_data['status'] = result['status']

    return order_data


@app.post("/v1/eats/orders/{order_id}/accept_pos_order")
async def accept_order(
        order_id: str,
        background_tasks: BackgroundTasks,
        token_info=Depends(verify_token)
):
    """Accept an order"""

    if "eats.order" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.order scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()

        # Verify order exists and is pending
        cursor.execute('''
            SELECT o.store_id, o.status, si.client_id
            FROM orders o
            JOIN store_integrations si ON o.store_id = si.store_id
            WHERE o.order_id = ? AND si.client_id = ?
        ''', (order_id, token_info['client_id']))

        result = cursor.fetchone()
        if not result:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Order not found"}
            )

        if result['status'] != 'pending':
            raise HTTPException(
                status_code=400,
                detail={"code": "bad_request", "message": f"Order is already {result['status']}"}
            )

        # Update order status
        cursor.execute('''
            UPDATE orders 
            SET status = 'accepted', updated_at = datetime('now')
            WHERE order_id = ?
        ''', (order_id,))
        conn.commit()

        store_id = result['store_id']

    # Trigger order accepted webhook
    background_tasks.add_task(
        trigger_webhook,
        "orders.status_update",
        store_id,
        order_id,
        {"status": "accepted"},
        delay=0.5
    )

    return {"message": "Order accepted", "order_id": order_id}


@app.post("/v1/eats/orders/{order_id}/deny_pos_order")
async def deny_order(
        order_id: str,
        data: OrderAction,
        background_tasks: BackgroundTasks,
        token_info=Depends(verify_token)
):
    """Deny an order"""

    if "eats.order" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.order scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()

        # Verify order exists and is pending
        cursor.execute('''
            SELECT o.store_id, o.status, si.client_id
            FROM orders o
            JOIN store_integrations si ON o.store_id = si.store_id
            WHERE o.order_id = ? AND si.client_id = ?
        ''', (order_id, token_info['client_id']))

        result = cursor.fetchone()
        if not result:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Order not found"}
            )

        if result['status'] != 'pending':
            raise HTTPException(
                status_code=400,
                detail={"code": "bad_request", "message": f"Order is already {result['status']}"}
            )

        # Update order status
        cursor.execute('''
            UPDATE orders 
            SET status = 'denied', updated_at = datetime('now')
            WHERE order_id = ?
        ''', (order_id,))
        conn.commit()

        store_id = result['store_id']

    # Trigger order denied webhook
    background_tasks.add_task(
        trigger_webhook,
        "orders.status_update",
        store_id,
        order_id,
        {"status": "denied", "reason": data.reason or "Unable to fulfill"},
        delay=0.5
    )

    return {
        "message": "Order denied",
        "order_id": order_id,
        "reason": data.reason or "Unable to fulfill"
    }


@app.post("/v1/eats/orders/{order_id}/cancel")
async def cancel_order(
        order_id: str,
        data: OrderAction,
        background_tasks: BackgroundTasks,
        token_info=Depends(verify_token)
):
    """Cancel an order"""

    if "eats.order" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.order scope"}
        )

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT o.store_id, si.client_id
            FROM orders o
            JOIN store_integrations si ON o.store_id = si.store_id
            WHERE o.order_id = ? AND si.client_id = ?
        ''', (order_id, token_info['client_id']))

        result = cursor.fetchone()
        if not result:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Order not found"}
            )

        cursor.execute('''
            UPDATE orders 
            SET status = 'cancelled', updated_at = datetime('now')
            WHERE order_id = ?
        ''', (order_id,))
        conn.commit()

        store_id = result['store_id']

    # Trigger order cancelled webhook
    background_tasks.add_task(
        trigger_webhook,
        "orders.cancel",
        store_id,
        order_id,
        {"status": "cancelled", "reason": data.reason},
        delay=0.5
    )

    return {
        "message": "Order cancelled",
        "order_id": order_id,
        "reason": data.reason
    }


# === MENU ENDPOINTS ===
@app.get("/v1/eats/stores/{store_id}/menus")
async def get_menu(store_id: str, token_info=Depends(verify_token)):
    """Get store menu"""

    if "eats.store" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.store scope"}
        )

    # Return mock menu
    return {
        "menus": [
            {
                "id": "menu_main",
                "title": "Main Menu",
                "service_availability": [
                    {
                        "day_of_week": "monday",
                        "time_periods": [
                            {"start_time": "08:00", "end_time": "22:00"}
                        ]
                    }
                ],
                "categories": [
                    {
                        "id": "cat_burgers",
                        "title": "Burgers",
                        "entities": [
                            {
                                "id": "item_classic",
                                "type": "ITEM",
                                "title": "Classic Burger",
                                "description": "Our signature beef burger",
                                "price": {"amount": 1299, "currency_code": "USD"},
                                "image_url": "https://example.com/classic.jpg"
                            },
                            {
                                "id": "item_cheese",
                                "type": "ITEM",
                                "title": "Cheese Burger",
                                "description": "Classic with cheese",
                                "price": {"amount": 1499, "currency_code": "USD"},
                                "image_url": "https://example.com/cheese.jpg"
                            }
                        ]
                    },
                    {
                        "id": "cat_drinks",
                        "title": "Beverages",
                        "entities": [
                            {
                                "id": "item_coke",
                                "type": "ITEM",
                                "title": "Coca Cola",
                                "price": {"amount": 299, "currency_code": "USD"}
                            },
                            {
                                "id": "item_water",
                                "type": "ITEM",
                                "title": "Water",
                                "price": {"amount": 199, "currency_code": "USD"}
                            }
                        ]
                    }
                ]
            }
        ]
    }


@app.put("/v1/eats/stores/{store_id}/menus")
async def upload_menu(
        store_id: str,
        menu_data: dict,
        token_info=Depends(verify_token)
):
    """Upload/update menu"""

    if "eats.store" not in token_info['scope']:
        raise HTTPException(
            status_code=403,
            detail={"code": "unauthorized", "message": "Requires eats.store scope"}
        )

    # In a real implementation, this would store the menu
    return {"message": "Menu uploaded successfully", "store_id": store_id}


# === SIMULATION ENDPOINTS ===
@app.post("/simulate/order")
async def simulate_order(order: MockOrder, background_tasks: BackgroundTasks):
    """Create a simulated order for testing"""

    order_id = f"order_{uuid.uuid4().hex[:12]}"

    # Create realistic order data
    order_data = {
        "id": order_id,
        "display_id": f"#{order_id[-4:].upper()}",
        "external_reference_id": f"EXT{order_id[-6:]}",
        "current_state": "CREATED",
        "type": "DELIVERY",
        "brand": "UBER_EATS",
        "store": {
            "id": order.store_id,
            "name": "Demo Restaurant"
        },
        "eater": {
            "first_name": order.customer_name.split()[0],
            "last_name": order.customer_name.split()[-1] if len(order.customer_name.split()) > 1 else "",
            "phone": "+1234567890",
            "phone_code": "1234"
        },
        "payment": {
            "charges": {
                "total": {
                    "amount": int(order.total * 100),
                    "currency_code": "USD"
                },
                "sub_total": {
                    "amount": int(order.total * 0.9 * 100),
                    "currency_code": "USD"
                },
                "tax": {
                    "amount": int(order.total * 0.1 * 100),
                    "currency_code": "USD"
                }
            }
        },
        "placed_at": datetime.now().isoformat(),
        "estimated_ready_for_pickup_at": (datetime.now() + timedelta(minutes=15)).isoformat(),
        "cart": {
            "items": order.items or [
                {
                    "id": str(uuid.uuid4()),
                    "instance_id": str(uuid.uuid4()),
                    "title": "Classic Burger",
                    "quantity": 1,
                    "price": {
                        "amount": int(order.total * 0.9 * 100),
                        "currency_code": "USD"
                    },
                    "special_instructions": "No pickles please"
                }
            ]
        }
    }

    with get_db() as conn:
        cursor = conn.cursor()

        # Create order
        cursor.execute('''
            INSERT INTO orders (order_id, store_id, order_data, status)
            VALUES (?, ?, ?, ?)
        ''', (order_id, order.store_id, json.dumps(order_data), 'pending'))

        # Check if store has integration
        cursor.execute('''
            SELECT webhook_url, require_manual_acceptance 
            FROM store_integrations 
            WHERE store_id = ? AND integration_enabled = 1
        ''', (order.store_id,))
        result = cursor.fetchone()

        conn.commit()

    # Trigger new order webhook if integration exists
    if result and result['webhook_url']:
        background_tasks.add_task(
            trigger_webhook,
            "orders.notification",
            order.store_id,
            order_id,
            {"status": "created"},
            delay=2.0  # Realistic delay
        )

    return {
        "message": "Order created",
        "order_id": order_id,
        "webhook_scheduled": bool(result and result['webhook_url'])
    }


@app.post("/simulate/delivery_update/{order_id}")
async def simulate_delivery_update(
        order_id: str,
        state: str,
        background_tasks: BackgroundTasks
):
    """Simulate delivery status update"""

    valid_states = ["COURIER_ASSIGNED", "COURIER_EN_ROUTE", "COURIER_ARRIVED", "PICKED_UP", "DELIVERED"]
    if state not in valid_states:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state. Must be one of: {', '.join(valid_states)}"
        )

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT store_id FROM orders WHERE order_id = ?', (order_id,))
        result = cursor.fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Order not found")

        store_id = result['store_id']

    # Trigger delivery status webhook
    background_tasks.add_task(
        trigger_webhook,
        "delivery.state_changed",
        store_id,
        order_id,
        {"delivery_state": state},
        delay=1.0
    )

    return {"message": f"Delivery update scheduled", "state": state}


# === WEBHOOK MANAGEMENT ===
@app.get("/webhooks/events")
async def get_webhook_events(
        status: Optional[str] = None,
        limit: int = 100
):
    """Get webhook events for debugging"""

    query = "SELECT * FROM webhook_events"
    params = []

    if status:
        query += " WHERE status = ?"
        params.append(status)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        events = cursor.fetchall()

    return {
        "events": [
            {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "store_id": event["store_id"],
                "order_id": event["order_id"],
                "status": event["status"],
                "attempts": event["attempts"],
                "webhook_url": event["webhook_url"],
                "created_at": event["created_at"]
            }
            for event in events
        ]
    }


@app.post("/webhooks/retry/{event_id}")
async def retry_webhook(event_id: str, background_tasks: BackgroundTasks):
    """Manually retry a failed webhook"""

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT webhook_url, payload 
            FROM webhook_events 
            WHERE event_id = ?
        ''', (event_id,))
        result = cursor.fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Event not found")

        # Reset status
        cursor.execute('''
            UPDATE webhook_events 
            SET status = 'pending', attempts = 0
            WHERE event_id = ?
        ''', (event_id,))
        conn.commit()

    # Retry webhook
    payload = json.loads(result['payload'])
    background_tasks.add_task(
        send_webhook_with_retry,
        event_id,
        result['webhook_url'],
        payload
    )

    return {"message": "Webhook retry scheduled"}


# === ROOT & HEALTH ===
@app.get("/")
async def root():
    """API information"""
    return {
        "name": "Uber Eats Mock Server",
        "version": "2.0.0",
        "endpoints": {
            "auth": {
                "authorize": "/oauth/v2/authorize",
                "token": "/oauth/v2/token"
            },
            "stores": "/v1/eats/stores",
            "orders": "/v1/eats/orders",
            "integration": "/v1/eats/stores/{store_id}/pos_data",
            "menu": "/v1/eats/stores/{store_id}/menus"
        },
        "simulation": {
            "create_order": "/simulate/order",
            "delivery_update": "/simulate/delivery_update/{order_id}"
        },
        "webhooks": {
            "events": "/webhooks/events",
            "retry": "/webhooks/retry/{event_id}"
        },
        "demo_credentials": {
            "client_id": "demo_client_id",
            "client_secret": "demo_client_secret"
        }
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM stores")
            store_count = cursor.fetchone()[0]

        return {
            "status": "healthy",
            "database": "connected",
            "stores": store_count
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


# === STARTUP & CLEANUP ===
@app.on_event("startup")
async def startup():
    """Initialize database on startup"""
    init_db()
    print(f"Uber Eats Mock Server v2.0 started!")
    print(f"Database: {DB_FILE}")
    print(f"API Docs: http://localhost:8000/docs")
    print(f"Demo credentials: demo_client_id / demo_client_secret")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    print("Shutting down mock server...")


# === BACKGROUND TASKS ===
async def webhook_retry_scheduler():
    """Background task to retry failed webhooks"""
    while True:
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT event_id, webhook_url, payload 
                    FROM webhook_events 
                    WHERE status = 'retrying' 
                    AND next_retry_at <= datetime('now')
                    LIMIT 10
                ''')
                events = cursor.fetchall()

            for event in events:
                payload = json.loads(event['payload'])
                await send_webhook_with_retry(
                    event['event_id'],
                    event['webhook_url'],
                    payload
                )

        except Exception as e:
            print(f"Webhook retry scheduler error: {e}")

        await asyncio.sleep(30)  # Check every 30 seconds


if __name__ == "__main__":
    import uvicorn

    # Run with gunicorn in production:
    # gunicorn app:app -c gunicorn_config.py

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info"
    )