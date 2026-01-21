"""
AVS-Shipday Integration Module
Handles address verification and Shipday delivery management integration.
"""

import os
import re
import logging
import requests
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Address:
    """Represents a parsed and validated address."""
    street: str
    city: str
    state: str
    zip_code: str
    country: str = "US"
    apartment: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "street": self.street,
            "city": self.city,
            "state": self.state,
            "zip_code": self.zip_code,
            "country": self.country,
            "apartment": self.apartment,
            "latitude": self.latitude,
            "longitude": self.longitude
        }

    def format_full(self) -> str:
        """Return full formatted address string."""
        parts = [self.street]
        if self.apartment:
            parts.append(f"Apt {self.apartment}")
        parts.append(f"{self.city}, {self.state} {self.zip_code}")
        return ", ".join(parts)


@dataclass
class VerificationResult:
    """Result of address verification."""
    is_valid: bool
    original_address: str
    verified_address: Optional[Address] = None
    confidence_score: float = 0.0
    error_message: Optional[str] = None
    suggestions: Optional[list] = None


class AVSClient:
    """Address Verification System client using Google Maps Geocoding API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GOOGLE_MAPS_API_KEY")
        if not self.api_key:
            raise ValueError("Google Maps API key is required")
        self.base_url = "https://maps.googleapis.com/maps/api/geocode/json"

    def verify_address(self, address: str) -> VerificationResult:
        """
        Verify and standardize an address using Google Maps Geocoding API.

        Args:
            address: Raw address string to verify

        Returns:
            VerificationResult with verification status and standardized address
        """
        try:
            params = {
                "address": address,
                "key": self.api_key,
                "components": "country:US"
            }

            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data["status"] == "OK" and data["results"]:
                result = data["results"][0]
                parsed = self._parse_geocode_result(result)

                if parsed:
                    return VerificationResult(
                        is_valid=True,
                        original_address=address,
                        verified_address=parsed,
                        confidence_score=self._calculate_confidence(result),
                        suggestions=None
                    )

            # Handle partial matches or no results
            suggestions = self._get_suggestions(data) if data.get("results") else None
            return VerificationResult(
                is_valid=False,
                original_address=address,
                error_message=f"Address verification failed: {data.get('status', 'UNKNOWN')}",
                suggestions=suggestions
            )

        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            return VerificationResult(
                is_valid=False,
                original_address=address,
                error_message=f"API request failed: {str(e)}"
            )

    def _parse_geocode_result(self, result: Dict) -> Optional[Address]:
        """Parse Google Geocoding API result into Address object."""
        components = {comp["types"][0]: comp for comp in result.get("address_components", [])}

        street_number = components.get("street_number", {}).get("long_name", "")
        route = components.get("route", {}).get("long_name", "")
        city = (components.get("locality") or components.get("sublocality") or
                components.get("administrative_area_level_2", {})).get("long_name", "")
        state = components.get("administrative_area_level_1", {}).get("short_name", "")
        zip_code = components.get("postal_code", {}).get("long_name", "")

        if not all([route, city, state, zip_code]):
            return None

        location = result.get("geometry", {}).get("location", {})

        return Address(
            street=f"{street_number} {route}".strip(),
            city=city,
            state=state,
            zip_code=zip_code,
            latitude=location.get("lat"),
            longitude=location.get("lng")
        )

    def _calculate_confidence(self, result: Dict) -> float:
        """Calculate confidence score based on geocoding result quality."""
        location_type = result.get("geometry", {}).get("location_type", "")

        confidence_map = {
            "ROOFTOP": 1.0,
            "RANGE_INTERPOLATED": 0.8,
            "GEOMETRIC_CENTER": 0.6,
            "APPROXIMATE": 0.4
        }

        base_score = confidence_map.get(location_type, 0.3)

        # Adjust for partial match
        if result.get("partial_match"):
            base_score *= 0.7

        return round(base_score, 2)

    def _get_suggestions(self, data: Dict) -> Optional[list]:
        """Extract address suggestions from partial matches."""
        if not data.get("results"):
            return None
        return [r.get("formatted_address") for r in data["results"][:3]]


class ShipdayClient:
    """Shipday API client for delivery management."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SHIPDAY_API_KEY")
        if not self.api_key:
            raise ValueError("Shipday API key is required")
        self.base_url = "https://api.shipday.com"
        self.headers = {
            "Authorization": f"Basic {self.api_key}",
            "Content-Type": "application/json"
        }

    def create_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new delivery order in Shipday.

        Args:
            order_data: Order details including customer info, items, and delivery address

        Returns:
            API response with order confirmation
        """
        url = f"{self.base_url}/orders"

        try:
            response = requests.post(url, json=order_data, headers=self.headers, timeout=30)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.RequestException as e:
            logger.error(f"Failed to create Shipday order: {e}")
            return {"success": False, "error": str(e)}

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Get the current status of an order."""
        url = f"{self.base_url}/orders/{order_id}"

        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.RequestException as e:
            logger.error(f"Failed to get order status: {e}")
            return {"success": False, "error": str(e)}

    def upload_delivery_photo(self, order_id: str, photo_url: str,
                              photo_type: str = "proof_of_delivery") -> Dict[str, Any]:
        """
        Upload a delivery photo for proof of delivery.

        Args:
            order_id: The Shipday order ID
            photo_url: URL of the photo to attach
            photo_type: Type of photo (proof_of_delivery, signature, etc.)

        Returns:
            API response confirming photo upload
        """
        url = f"{self.base_url}/orders/{order_id}/photos"
        payload = {
            "photoUrl": photo_url,
            "photoType": photo_type,
            "timestamp": datetime.utcnow().isoformat()
        }

        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.RequestException as e:
            logger.error(f"Failed to upload delivery photo: {e}")
            return {"success": False, "error": str(e)}

    def update_order_status(self, order_id: str, status: str,
                            notes: Optional[str] = None) -> Dict[str, Any]:
        """Update the status of an existing order."""
        url = f"{self.base_url}/orders/{order_id}/status"
        payload = {"status": status}
        if notes:
            payload["notes"] = notes

        try:
            response = requests.put(url, json=payload, headers=self.headers, timeout=10)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.RequestException as e:
            logger.error(f"Failed to update order status: {e}")
            return {"success": False, "error": str(e)}

    def get_carrier_location(self, carrier_id: str) -> Dict[str, Any]:
        """Get current location of a delivery carrier."""
        url = f"{self.base_url}/carriers/{carrier_id}/location"

        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.RequestException as e:
            logger.error(f"Failed to get carrier location: {e}")
            return {"success": False, "error": str(e)}


class AVSShipdayIntegration:
    """
    Main integration class combining AVS verification with Shipday delivery management.
    """

    def __init__(self, google_api_key: Optional[str] = None,
                 shipday_api_key: Optional[str] = None):
        self.avs = AVSClient(google_api_key)
        self.shipday = ShipdayClient(shipday_api_key)

    def create_verified_delivery(self, customer_name: str, phone: str,
                                  email: str, address: str,
                                  items: list, order_number: str,
                                  special_instructions: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a delivery order with verified address.

        Args:
            customer_name: Customer's full name
            phone: Customer's phone number
            email: Customer's email address
            address: Delivery address to verify
            items: List of order items
            order_number: Unique order identifier
            special_instructions: Optional delivery instructions

        Returns:
            Result containing verification status and order details
        """
        # Step 1: Verify the address
        verification = self.avs.verify_address(address)

        if not verification.is_valid:
            return {
                "success": False,
                "stage": "address_verification",
                "error": verification.error_message,
                "suggestions": verification.suggestions
            }

        verified = verification.verified_address

        # Step 2: Build Shipday order payload
        order_payload = {
            "orderNumber": order_number,
            "customerName": customer_name,
            "customerPhone": self._format_phone(phone),
            "customerEmail": email,
            "deliveryAddress": {
                "street": verified.street,
                "city": verified.city,
                "state": verified.state,
                "zipCode": verified.zip_code,
                "country": verified.country,
                "unit": verified.apartment
            },
            "deliveryLatitude": verified.latitude,
            "deliveryLongitude": verified.longitude,
            "orderItems": items,
            "deliveryInstruction": special_instructions or "",
            "tips": 0,
            "tax": 0,
            "discounts": 0,
            "orderSource": "AVS Integration"
        }

        # Step 3: Create order in Shipday
        order_result = self.shipday.create_order(order_payload)

        if not order_result["success"]:
            return {
                "success": False,
                "stage": "order_creation",
                "error": order_result["error"],
                "verified_address": verified.to_dict()
            }

        return {
            "success": True,
            "verification": {
                "confidence_score": verification.confidence_score,
                "original_address": verification.original_address,
                "verified_address": verified.to_dict()
            },
            "order": order_result["data"]
        }

    def process_delivery_completion(self, order_id: str, photo_url: str,
                                     signature_url: Optional[str] = None,
                                     notes: Optional[str] = None) -> Dict[str, Any]:
        """
        Process delivery completion with photo proof.

        Args:
            order_id: Shipday order ID
            photo_url: URL of proof of delivery photo
            signature_url: Optional URL of customer signature
            notes: Optional delivery notes

        Returns:
            Result of completion processing
        """
        results = {"order_id": order_id, "photos": [], "status_update": None}

        # Upload proof of delivery photo
        photo_result = self.shipday.upload_delivery_photo(
            order_id, photo_url, "proof_of_delivery"
        )
        results["photos"].append({
            "type": "proof_of_delivery",
            "success": photo_result["success"],
            "data": photo_result.get("data"),
            "error": photo_result.get("error")
        })

        # Upload signature if provided
        if signature_url:
            sig_result = self.shipday.upload_delivery_photo(
                order_id, signature_url, "signature"
            )
            results["photos"].append({
                "type": "signature",
                "success": sig_result["success"],
                "data": sig_result.get("data"),
                "error": sig_result.get("error")
            })

        # Update order status to delivered
        status_result = self.shipday.update_order_status(
            order_id, "DELIVERED", notes
        )
        results["status_update"] = status_result

        results["success"] = all(p["success"] for p in results["photos"]) and status_result["success"]

        return results

    def _format_phone(self, phone: str) -> str:
        """Format phone number to standard format."""
        digits = re.sub(r'\D', '', phone)
        if len(digits) == 10:
            return f"+1{digits}"
        elif len(digits) == 11 and digits.startswith('1'):
            return f"+{digits}"
        return phone


# Convenience function for quick verification
def verify_address(address: str, api_key: Optional[str] = None) -> VerificationResult:
    """Quick address verification helper."""
    client = AVSClient(api_key)
    return client.verify_address(address)


if __name__ == "__main__":
    # Example usage
    import json

    # Test address verification
    test_address = "1600 Amphitheatre Parkway, Mountain View, CA"

    print("Testing AVS-Shipday Integration")
    print("=" * 50)

    try:
        result = verify_address(test_address)
        print(f"\nVerification Result:")
        print(f"  Valid: {result.is_valid}")
        print(f"  Confidence: {result.confidence_score}")
        if result.verified_address:
            print(f"  Address: {result.verified_address.format_full()}")
            print(f"  Coordinates: ({result.verified_address.latitude}, {result.verified_address.longitude})")
    except ValueError as e:
        print(f"Configuration error: {e}")
        print("Please set GOOGLE_MAPS_API_KEY environment variable")
