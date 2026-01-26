"""
Enhanced Flask Service for AVS-Shipday Integration
Provides REST API endpoints for address verification and delivery management.
"""

import os
import re
import json
import logging
import hmac
import hashlib
from functools import wraps
from datetime import datetime
from typing import Optional, Dict, Any

from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from flasgger import Swagger, swag_from

from avs_shipday_integration import (
    AVSClient, ShipdayClient, AVSShipdayIntegration,
    VerificationResult, Address
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app, origins=os.getenv("CORS_ORIGINS", "*").split(","))

# Configuration
app.config.update(
    JSON_SORT_KEYS=False,
    MAX_CONTENT_LENGTH=16 * 1024 * 1024  # 16MB max for photo uploads
)

# Swagger configuration
swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/apispec.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/docs"
}

swagger_template = {
    "swagger": "2.0",
    "info": {
        "title": "AVS-Shipday Integration API",
        "description": "REST API for address verification and delivery management with Shipday integration.",
        "version": "1.0.0",
        "contact": {
            "name": "API Support"
        }
    },
    "securityDefinitions": {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "API key for authentication"
        },
        "BearerAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Bearer token (format: Bearer <api_key>)"
        }
    },
    "tags": [
        {"name": "Health", "description": "Health check endpoints"},
        {"name": "Address Verification", "description": "Address verification endpoints"},
        {"name": "Vendor", "description": "Vendor address verification endpoints"},
        {"name": "Orders", "description": "Order management endpoints"},
        {"name": "Webhooks", "description": "Webhook handlers"}
    ]
}

swagger = Swagger(app, config=swagger_config, template=swagger_template)

# API key for authentication
API_KEY = os.getenv("API_KEY")
SHIPDAY_WEBHOOK_SECRET = os.getenv("SHIPDAY_WEBHOOK_SECRET")


def require_api_key(f):
    """Decorator to require API key authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not API_KEY:
            # API key not configured, skip authentication
            return f(*args, **kwargs)

        auth_header = request.headers.get("Authorization", "")
        api_key_header = request.headers.get("X-API-Key", "")

        # Check Bearer token or X-API-Key header
        if auth_header.startswith("Bearer "):
            provided_key = auth_header[7:]
        else:
            provided_key = api_key_header

        if not provided_key or provided_key != API_KEY:
            return jsonify({"error": "Unauthorized", "message": "Invalid or missing API key"}), 401

        return f(*args, **kwargs)
    return decorated


def verify_shipday_webhook(f):
    """Decorator to verify Shipday webhook token from multiple headers."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not SHIPDAY_WEBHOOK_SECRET:
            logger.warning("Webhook secret not configured, skipping verification")
            return f(*args, **kwargs)

        # Check multiple headers for the webhook token
        webhook_token = request.headers.get("X-Webhook-Token", "")
        shipday_signature = request.headers.get("X-Shipday-Signature", "")
        auth_header = request.headers.get("Authorization", "")

        # Extract token from Authorization header (Bearer token or raw)
        auth_token = ""
        if auth_header.startswith("Bearer "):
            auth_token = auth_header[7:]
        else:
            auth_token = auth_header

        # Check if any of the provided tokens match the secret
        provided_tokens = [webhook_token, shipday_signature, auth_token]
        token_valid = any(
            token and hmac.compare_digest(token, SHIPDAY_WEBHOOK_SECRET)
            for token in provided_tokens
        )

        if not token_valid:
            logger.warning("Invalid webhook token received")
            return jsonify({"error": "Unauthorized", "message": "Invalid or missing webhook token"}), 401

        return f(*args, **kwargs)
    return decorated


@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler."""
    if isinstance(e, HTTPException):
        return jsonify({"error": e.name, "message": e.description}), e.code

    logger.exception("Unhandled exception")
    return jsonify({
        "error": "Internal Server Error",
        "message": "An unexpected error occurred"
    }), 500


# Initialize clients lazily
_avs_client: Optional[AVSClient] = None
_shipday_client: Optional[ShipdayClient] = None
_integration: Optional[AVSShipdayIntegration] = None


def get_avs_client() -> AVSClient:
    global _avs_client
    if _avs_client is None:
        _avs_client = AVSClient()
    return _avs_client


def get_shipday_client() -> ShipdayClient:
    global _shipday_client
    if _shipday_client is None:
        _shipday_client = ShipdayClient()
    return _shipday_client


def get_integration() -> AVSShipdayIntegration:
    global _integration
    if _integration is None:
        _integration = AVSShipdayIntegration()
    return _integration


# Health check endpoint
@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for load balancers.
    ---
    tags:
      - Health
    responses:
      200:
        description: Service is healthy
        schema:
          type: object
          properties:
            status:
              type: string
              example: healthy
            timestamp:
              type: string
              example: "2025-01-25T10:00:00"
            service:
              type: string
              example: avs-shipday-integration
    """
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "avs-shipday-integration"
    })


@app.route("/", methods=["GET"])
def root():
    """Root endpoint with API information."""
    return jsonify({
        "service": "AVS-Shipday Integration API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "verify_address": "/api/v1/verify-address",
            "vendor_address_verification": "/vendor/address-verification/submit",
            "create_order": "/api/v1/orders",
            "get_order": "/api/v1/orders/<order_id>",
            "complete_delivery": "/api/v1/orders/<order_id>/complete",
            "upload_photo": "/api/v1/orders/<order_id>/photos",
            "webhook": "/webhooks/shipday"
        }
    })


# Address Verification Endpoints
@app.route("/api/v1/verify-address", methods=["POST"])
@require_api_key
def verify_address():
    """Verify and standardize a delivery address.
    ---
    tags:
      - Address Verification
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - address
          properties:
            address:
              type: string
              example: "123 Main St, City, State 12345"
    responses:
      200:
        description: Address verification result
        schema:
          type: object
          properties:
            success:
              type: boolean
            original_address:
              type: string
            confidence_score:
              type: number
            verified_address:
              type: object
              properties:
                street:
                  type: string
                city:
                  type: string
                state:
                  type: string
                zip_code:
                  type: string
                country:
                  type: string
                latitude:
                  type: number
                longitude:
                  type: number
            formatted_address:
              type: string
      400:
        description: Bad request - address is required
      401:
        description: Unauthorized - invalid or missing API key
      500:
        description: Configuration error
    """
    data = request.get_json()
    if not data or "address" not in data:
        return jsonify({"error": "Bad Request", "message": "Address is required"}), 400

    address = data["address"].strip()
    if not address:
        return jsonify({"error": "Bad Request", "message": "Address cannot be empty"}), 400

    try:
        avs = get_avs_client()
        result = avs.verify_address(address)

        response = {
            "success": result.is_valid,
            "original_address": result.original_address,
            "confidence_score": result.confidence_score
        }

        if result.is_valid and result.verified_address:
            response["verified_address"] = result.verified_address.to_dict()
            response["formatted_address"] = result.verified_address.format_full()
        else:
            response["error"] = result.error_message
            if result.suggestions:
                response["suggestions"] = result.suggestions

        return jsonify(response)

    except ValueError as e:
        return jsonify({"error": "Configuration Error", "message": str(e)}), 500


@app.route("/api/v1/verify-address/batch", methods=["POST"])
@require_api_key
def verify_addresses_batch():
    """Verify multiple addresses in a single request.
    ---
    tags:
      - Address Verification
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - addresses
          properties:
            addresses:
              type: array
              items:
                type: string
              example: ["123 Main St, City, State 12345", "456 Oak Ave, Town, State 67890"]
    responses:
      200:
        description: Batch verification results
        schema:
          type: object
          properties:
            success:
              type: boolean
            count:
              type: integer
            results:
              type: array
              items:
                type: object
                properties:
                  original:
                    type: string
                  is_valid:
                    type: boolean
                  confidence_score:
                    type: number
                  verified_address:
                    type: object
                  error:
                    type: string
      400:
        description: Bad request - addresses array is required
      401:
        description: Unauthorized - invalid or missing API key
      500:
        description: Configuration error
    """
    data = request.get_json()
    if not data or "addresses" not in data:
        return jsonify({"error": "Bad Request", "message": "Addresses array is required"}), 400

    addresses = data["addresses"]
    if not isinstance(addresses, list) or len(addresses) == 0:
        return jsonify({"error": "Bad Request", "message": "Addresses must be a non-empty array"}), 400

    if len(addresses) > 50:
        return jsonify({"error": "Bad Request", "message": "Maximum 50 addresses per request"}), 400

    try:
        avs = get_avs_client()
        results = []

        for addr in addresses:
            result = avs.verify_address(addr)
            results.append({
                "original": addr,
                "is_valid": result.is_valid,
                "confidence_score": result.confidence_score,
                "verified_address": result.verified_address.to_dict() if result.verified_address else None,
                "error": result.error_message
            })

        return jsonify({
            "success": True,
            "count": len(results),
            "results": results
        })

    except ValueError as e:
        return jsonify({"error": "Configuration Error", "message": str(e)}), 500


@app.route("/vendor/address-verification/submit", methods=["POST"])
@require_api_key
def vendor_address_verification_submit():
    """Submit an address verification request.
    ---
    tags:
      - Vendor
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - customer_reference
            - subject_first_name
            - subject_last_name
            - address_street
            - address_city
            - address_state
            - address_country
          properties:
            customer_reference:
              type: string
              description: Unique reference for the customer/request
              example: "CUST-12345"
            verification_type:
              type: string
              description: Type of verification
              default: "physical_address"
              example: "physical_address"
            subject_first_name:
              type: string
              description: First name of the subject
              example: "John"
            subject_last_name:
              type: string
              description: Last name of the subject
              example: "Doe"
            subject_middle_name:
              type: string
              description: Middle name of the subject
              example: "Michael"
            subject_email:
              type: string
              format: email
              description: Email address of the subject
              example: "john.doe@example.com"
            subject_phone:
              type: string
              description: Phone number of the subject
              example: "+2348012345678"
            subject_date_of_birth:
              type: string
              description: Date of birth of the subject
              example: "1990-01-15"
            address_street:
              type: string
              description: Street address
              example: "123 Main Street"
            address_city:
              type: string
              description: City
              example: "Lagos"
            address_state:
              type: string
              description: State
              example: "Lagos"
            address_lga:
              type: string
              description: Local Government Area
              example: "Ikeja"
            address_landmark:
              type: string
              description: Nearby landmark
              example: "Near Central Mosque"
            address_postal_code:
              type: string
              description: Postal code
              example: "100001"
            address_country:
              type: string
              description: Country
              example: "Nigeria"
    responses:
      200:
        description: Address verification request submitted successfully
        schema:
          type: object
          properties:
            status:
              type: string
              description: Status of the request
              example: "pending"
            message:
              type: string
              description: Response message
              example: "Address verification request submitted successfully"
            reference:
              type: string
              description: Reference ID for tracking the verification
              example: "AVS-2025-0001"
      400:
        description: Bad request - missing required fields
      401:
        description: Unauthorized - invalid or missing API key
      500:
        description: Configuration error
    """
    import uuid

    data = request.get_json()
    if not data:
        return jsonify({
            "status": "error",
            "message": "Request body is required",
            "reference": ""
        }), 400

    # Validate required fields
    required_fields = [
        "customer_reference",
        "subject_first_name",
        "subject_last_name",
        "address_street",
        "address_city",
        "address_state",
        "address_country"
    ]

    missing_fields = [field for field in required_fields if not data.get(field)]
    if missing_fields:
        return jsonify({
            "status": "error",
            "message": f"Missing required fields: {', '.join(missing_fields)}",
            "reference": ""
        }), 400

    # Extract address fields
    customer_reference = data.get("customer_reference", "")
    verification_type = data.get("verification_type", "physical_address")

    # Subject information
    subject_info = {
        "first_name": data.get("subject_first_name", ""),
        "last_name": data.get("subject_last_name", ""),
        "middle_name": data.get("subject_middle_name", ""),
        "email": data.get("subject_email", ""),
        "phone": data.get("subject_phone", ""),
        "date_of_birth": data.get("subject_date_of_birth", "")
    }

    # Address information
    address_info = {
        "street": data.get("address_street", ""),
        "city": data.get("address_city", ""),
        "state": data.get("address_state", ""),
        "lga": data.get("address_lga", ""),
        "landmark": data.get("address_landmark", ""),
        "postal_code": data.get("address_postal_code", ""),
        "country": data.get("address_country", "")
    }

    # Build full address string for verification
    address_parts = [
        address_info["street"],
        address_info["lga"],
        address_info["city"],
        address_info["state"],
        address_info["postal_code"],
        address_info["country"]
    ]
    full_address = ", ".join(part for part in address_parts if part)

    # Generate a unique reference for this verification request
    verification_reference = f"AVS-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:8].upper()}"

    try:
        # Log the verification request
        logger.info(f"Address verification request received: {verification_reference}")
        logger.info(f"Customer reference: {customer_reference}")
        logger.info(f"Subject: {subject_info['first_name']} {subject_info['last_name']}")
        logger.info(f"Address: {full_address}")

        # TODO: Integrate with actual address verification service
        # For now, we acknowledge receipt and return pending status

        return jsonify({
            "status": "pending",
            "message": "Address verification request submitted successfully",
            "reference": verification_reference
        }), 200

    except Exception as e:
        logger.exception(f"Error processing address verification request: {str(e)}")
        return jsonify({
            "status": "error",
            "message": "An error occurred while processing the request",
            "reference": ""
        }), 500


# Order Management Endpoints
@app.route("/api/v1/orders", methods=["POST"])
@require_api_key
def create_order():
    """Create a new delivery order with address verification.
    ---
    tags:
      - Orders
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - order_number
            - customer
            - delivery_address
            - items
          properties:
            order_number:
              type: string
              example: "ORD-12345"
            customer:
              type: object
              required:
                - name
                - phone
                - email
              properties:
                name:
                  type: string
                  example: "John Doe"
                phone:
                  type: string
                  example: "+1234567890"
                email:
                  type: string
                  example: "john@example.com"
            delivery_address:
              type: string
              example: "123 Main St, City, State 12345"
            items:
              type: array
              items:
                type: object
                properties:
                  name:
                    type: string
                  quantity:
                    type: integer
                  price:
                    type: number
              example: [{"name": "Item 1", "quantity": 1, "price": 10.00}]
            special_instructions:
              type: string
              example: "Leave at door"
    responses:
      201:
        description: Order created successfully
      400:
        description: Bad request or address verification failed
      401:
        description: Unauthorized - invalid or missing API key
      500:
        description: Configuration error
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Bad Request", "message": "Request body is required"}), 400

    # Validate required fields
    required = ["order_number", "customer", "delivery_address", "items"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({
            "error": "Bad Request",
            "message": f"Missing required fields: {', '.join(missing)}"
        }), 400

    customer = data["customer"]
    if not all(k in customer for k in ["name", "phone", "email"]):
        return jsonify({
            "error": "Bad Request",
            "message": "Customer must include name, phone, and email"
        }), 400

    try:
        integration = get_integration()
        result = integration.create_verified_delivery(
            customer_name=customer["name"],
            phone=customer["phone"],
            email=customer["email"],
            address=data["delivery_address"],
            items=data["items"],
            order_number=data["order_number"],
            special_instructions=data.get("special_instructions")
        )

        status_code = 201 if result["success"] else 400
        return jsonify(result), status_code

    except ValueError as e:
        return jsonify({"error": "Configuration Error", "message": str(e)}), 500


@app.route("/api/v1/orders/<order_id>", methods=["GET"])
@require_api_key
def get_order(order_id: str):
    """Get order details and status.
    ---
    tags:
      - Orders
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: path
        name: order_id
        type: string
        required: true
        description: The order ID
    responses:
      200:
        description: Order details
      401:
        description: Unauthorized - invalid or missing API key
      404:
        description: Order not found
      500:
        description: Configuration error
    """
    try:
        shipday = get_shipday_client()
        result = shipday.get_order_status(order_id)

        if result["success"]:
            return jsonify(result["data"])
        else:
            return jsonify({"error": "Not Found", "message": result["error"]}), 404

    except ValueError as e:
        return jsonify({"error": "Configuration Error", "message": str(e)}), 500


@app.route("/api/v1/orders/<order_id>/status", methods=["PUT"])
@require_api_key
def update_order_status(order_id: str):
    """Update order status.
    ---
    tags:
      - Orders
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: path
        name: order_id
        type: string
        required: true
        description: The order ID
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - status
          properties:
            status:
              type: string
              enum: [PENDING, ASSIGNED, PICKED_UP, EN_ROUTE, DELIVERED, CANCELLED]
              example: "DELIVERED"
            notes:
              type: string
              example: "Left at front door"
    responses:
      200:
        description: Order status updated
      400:
        description: Bad request - invalid status
      401:
        description: Unauthorized - invalid or missing API key
      500:
        description: Configuration error
    """
    data = request.get_json()
    if not data or "status" not in data:
        return jsonify({"error": "Bad Request", "message": "Status is required"}), 400

    valid_statuses = ["PENDING", "ASSIGNED", "PICKED_UP", "EN_ROUTE", "DELIVERED", "CANCELLED"]
    if data["status"] not in valid_statuses:
        return jsonify({
            "error": "Bad Request",
            "message": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        }), 400

    try:
        shipday = get_shipday_client()
        result = shipday.update_order_status(
            order_id,
            data["status"],
            data.get("notes")
        )

        if result["success"]:
            return jsonify(result["data"])
        else:
            return jsonify({"error": "Update Failed", "message": result["error"]}), 400

    except ValueError as e:
        return jsonify({"error": "Configuration Error", "message": str(e)}), 500


# Photo Upload and Delivery Completion
@app.route("/api/v1/orders/<order_id>/photos", methods=["POST"])
@require_api_key
def upload_photo(order_id: str):
    """Upload a delivery photo.
    ---
    tags:
      - Orders
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: path
        name: order_id
        type: string
        required: true
        description: The order ID
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - photo_url
          properties:
            photo_url:
              type: string
              format: uri
              example: "https://storage.example.com/photo.jpg"
            photo_type:
              type: string
              enum: [proof_of_delivery, signature, package, location]
              default: proof_of_delivery
              example: "proof_of_delivery"
    responses:
      200:
        description: Photo uploaded successfully
      400:
        description: Bad request - invalid photo type or missing URL
      401:
        description: Unauthorized - invalid or missing API key
      500:
        description: Configuration error
    """
    data = request.get_json()
    if not data or "photo_url" not in data:
        return jsonify({"error": "Bad Request", "message": "Photo URL is required"}), 400

    photo_type = data.get("photo_type", "proof_of_delivery")
    valid_types = ["proof_of_delivery", "signature", "package", "location"]
    if photo_type not in valid_types:
        return jsonify({
            "error": "Bad Request",
            "message": f"Invalid photo type. Must be one of: {', '.join(valid_types)}"
        }), 400

    try:
        shipday = get_shipday_client()
        result = shipday.upload_delivery_photo(
            order_id,
            data["photo_url"],
            photo_type
        )

        if result["success"]:
            return jsonify({
                "success": True,
                "order_id": order_id,
                "photo_type": photo_type,
                "data": result["data"]
            })
        else:
            return jsonify({"error": "Upload Failed", "message": result["error"]}), 400

    except ValueError as e:
        return jsonify({"error": "Configuration Error", "message": str(e)}), 500


@app.route("/api/v1/orders/<order_id>/complete", methods=["POST"])
@require_api_key
def complete_delivery(order_id: str):
    """Mark delivery as complete with proof photos.
    ---
    tags:
      - Orders
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: path
        name: order_id
        type: string
        required: true
        description: The order ID
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - photo_url
          properties:
            photo_url:
              type: string
              format: uri
              example: "https://storage.example.com/delivery-proof.jpg"
            signature_url:
              type: string
              format: uri
              example: "https://storage.example.com/signature.jpg"
            notes:
              type: string
              example: "Delivered to customer"
    responses:
      200:
        description: Delivery completed successfully
      400:
        description: Bad request or completion failed
      401:
        description: Unauthorized - invalid or missing API key
      500:
        description: Configuration error
    """
    data = request.get_json()
    if not data or "photo_url" not in data:
        return jsonify({"error": "Bad Request", "message": "Photo URL is required"}), 400

    try:
        integration = get_integration()
        result = integration.process_delivery_completion(
            order_id=order_id,
            photo_url=data["photo_url"],
            signature_url=data.get("signature_url"),
            notes=data.get("notes")
        )

        status_code = 200 if result["success"] else 400
        return jsonify(result), status_code

    except ValueError as e:
        return jsonify({"error": "Configuration Error", "message": str(e)}), 500


# Shipday Webhook Handler
@app.route("/webhooks/shipday", methods=["POST"])
@verify_shipday_webhook
def shipday_webhook():
    """Handle Shipday webhook events.
    ---
    tags:
      - Webhooks
    parameters:
      - in: header
        name: X-Webhook-Token
        type: string
        description: Webhook verification token
      - in: header
        name: X-Shipday-Signature
        type: string
        description: Alternative Shipday signature header
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            event:
              type: string
              enum: [order.status.changed, order.assigned, order.picked_up, order.delivered, carrier.location.updated]
              example: "order.status.changed"
            orderId:
              type: string
              example: "12345"
            newStatus:
              type: string
            oldStatus:
              type: string
            carrier:
              type: object
            timestamp:
              type: string
    responses:
      200:
        description: Webhook processed successfully
      400:
        description: Bad request - no payload
      401:
        description: Unauthorized - invalid webhook token
      500:
        description: Processing error
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Bad Request", "message": "No payload"}), 400

    event_type = data.get("event")
    logger.info(f"Received Shipday webhook: {event_type}")

    # Process different event types
    handlers = {
        "order.status.changed": handle_status_change,
        "order.assigned": handle_order_assigned,
        "order.picked_up": handle_order_picked_up,
        "order.delivered": handle_order_delivered,
        "carrier.location.updated": handle_carrier_location
    }

    handler = handlers.get(event_type)
    if handler:
        try:
            result = handler(data)
            return jsonify({"success": True, "result": result})
        except Exception as e:
            logger.exception(f"Error handling webhook {event_type}")
            return jsonify({"error": "Processing Error", "message": str(e)}), 500
    else:
        logger.warning(f"Unhandled webhook event: {event_type}")
        return jsonify({"success": True, "message": "Event acknowledged but not processed"})


def handle_status_change(data: Dict) -> Dict:
    """Handle order status change event."""
    order_id = data.get("orderId")
    new_status = data.get("newStatus")
    old_status = data.get("oldStatus")

    logger.info(f"Order {order_id} status changed: {old_status} -> {new_status}")

    # Add custom logic here (notifications, database updates, etc.)

    return {"order_id": order_id, "new_status": new_status}


def handle_order_assigned(data: Dict) -> Dict:
    """Handle order assigned to carrier event."""
    order_id = data.get("orderId")
    carrier = data.get("carrier", {})

    logger.info(f"Order {order_id} assigned to carrier {carrier.get('name')}")

    return {"order_id": order_id, "carrier": carrier.get("name")}


def handle_order_picked_up(data: Dict) -> Dict:
    """Handle order picked up event."""
    order_id = data.get("orderId")
    pickup_time = data.get("timestamp")

    logger.info(f"Order {order_id} picked up at {pickup_time}")

    return {"order_id": order_id, "pickup_time": pickup_time}


def handle_order_delivered(data: Dict) -> Dict:
    """Handle order delivered event."""
    order_id = data.get("orderId")
    delivery_time = data.get("timestamp")
    proof_photos = data.get("proofPhotos", [])

    logger.info(f"Order {order_id} delivered at {delivery_time} with {len(proof_photos)} photos")

    return {
        "order_id": order_id,
        "delivery_time": delivery_time,
        "photo_count": len(proof_photos)
    }


def handle_carrier_location(data: Dict) -> Dict:
    """Handle carrier location update event."""
    carrier_id = data.get("carrierId")
    location = data.get("location", {})

    logger.debug(f"Carrier {carrier_id} location: {location}")

    return {"carrier_id": carrier_id, "location": location}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    logger.info(f"Starting AVS-Shipday Integration Service on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
