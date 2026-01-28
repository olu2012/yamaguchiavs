"""
AVS-Shipday Integration Module
Handles bidirectional integration between Address Verification Service and Shipday API
"""

import requests
import json
import base64
from datetime import datetime
from typing import Dict, List, Optional
from enum import Enum


class VerificationStatus(Enum):
    """AVS Verification Status Enum"""
    PENDING = 1
    SUCCESS = 2
    FAILED = 3
    RETURNED = 4


class MediaType(Enum):
    """AVS Media Type Enum"""
    IMAGE = 1
    VIDEO = 2


class AVSShipdayIntegration:
    """Main integration class for AVS and Shipday"""

    def __init__(self,
                 shipday_api_key: str,
                 avs_vendor_id: str,
                 avs_subscription_key: str,
                 avs_base_url: str = "https://alat-dev-apim.azure-api.net/customops"):
        """
        Initialize the integration

        Args:
            shipday_api_key: Your Shipday API key
            avs_vendor_id: Your vendor ID provided by AVS (GUID)
            avs_subscription_key: Subscription key for AVS API
            avs_base_url: AVS API base URL (default is development)
        """
        self.shipday_api_key = shipday_api_key
        self.avs_vendor_id = avs_vendor_id
        self.avs_subscription_key = avs_subscription_key
        self.avs_base_url = avs_base_url

        self.shipday_base_url = "https://api.shipday.com"

    # ==================== PHASE 1: RECEIVE FROM AVS ====================

    def handle_avs_request(self, request_data: Dict) -> Dict:
        """
        Handle incoming address verification request from AVS
        Creates corresponding tasks in Shipday

        Args:
            request_data: The JSON payload from AVS

        Returns:
            Response dictionary with status and request ID
        """
        try:
            vendor_id = request_data.get('vendorId')
            verification_requests = request_data.get('addressVerificationResponses', [])

            if not vendor_id or not verification_requests:
                return {
                    "status": "error",
                    "message": "Invalid request: missing vendorId or verification requests",
                    "requestId": None
                }

            # Process each verification request
            created_tasks = []
            for request in verification_requests:
                activity_id = request.get('activityId')

                # Create Shipday delivery task
                shipday_order = self._create_shipday_order(request)

                if shipday_order:
                    created_tasks.append({
                        'activityId': activity_id,
                        'shipdayOrderId': shipday_order.get('orderId')
                    })

            # Return success response
            if created_tasks:
                return {
                    "status": "success",
                    "message": "AVR request has been submitted successfully. Please track status using the request ID provided.",
                    "requestId": verification_requests[0].get('activityId'),
                    "createdTasks": created_tasks
                }
            else:
                return {
                    "status": "error",
                    "message": "Failed to create Shipday orders",
                    "requestId": None
                }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Error processing request: {str(e)}",
                "requestId": None
            }

    def _create_shipday_order(self, verification_request: Dict) -> Optional[Dict]:
        """
        Create a delivery order in Shipday based on AVS verification request

        Args:
            verification_request: Single verification request from AVS

        Returns:
            Shipday order response or None if failed
        """
        try:
            # Extract address information
            customer_name = verification_request.get('customerName', '')
            address = verification_request.get('address', '')
            activity_id = verification_request.get('activityId', '')

            # Prepare Shipday order payload - minimal required fields
            shipday_payload = {
                "orderNumber": activity_id,
                "customerName": customer_name,
                "customerAddress": address,
                "customerPhoneNumber": "08000000000",
                "restaurantName": "AVS Verification HQ",
                "restaurantAddress": "Victoria Island, Lagos, Nigeria",
                "restaurantPhoneNumber": "08000000000"
            }

            # Make API call to Shipday
            headers = {
                "Authorization": f"Basic {self.shipday_api_key}",
                "Content-Type": "application/json"
            }

            shipday_url = f"{self.shipday_base_url}/orders"
            print(f"[Shipday] Creating order at: {shipday_url}")
            print(f"[Shipday] Payload: {json.dumps(shipday_payload)[:500]}")

            response = requests.post(
                shipday_url,
                headers=headers,
                json=shipday_payload
            )

            print(f"[Shipday] Response Status: {response.status_code}")
            print(f"[Shipday] Response Body: {response.text[:500] if response.text else 'empty'}")

            if response.status_code in [200, 201]:
                try:
                    result = response.json()
                    # Check if Shipday reported success
                    if result.get('success') == True:
                        return result
                    else:
                        print(f"[Shipday] API returned success=false: {result}")
                        return None
                except Exception as json_err:
                    print(f"[Shipday] Failed to parse JSON: {json_err}, raw: {response.text[:200]}")
                    return None
            else:
                print(f"Error creating Shipday order: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            print(f"Exception creating Shipday order: {str(e)}")
            return None

    # ==================== PHASE 2: SEND TO AVS ====================

    def submit_verification_result(self,
                                   activity_id: str,
                                   shipday_order_data: Dict,
                                   verification_details: Dict) -> Dict:
        """
        Submit completed verification result back to AVS

        Args:
            activity_id: The original activityId from AVS
            shipday_order_data: Completed order data from Shipday
            verification_details: Additional verification details collected

        Returns:
            Response from AVS API
        """
        try:
            # Construct AVS response payload wrapped in 'request' as required by AVS API
            avs_response = self._build_avs_response(activity_id, shipday_order_data, verification_details)

            # The AVS API expects the payload wrapped in a 'request' object
            avs_payload = {
                "request": {
                    "vendorId": self.avs_vendor_id,
                    "addressVerificationResponses": [avs_response]
                }
            }

            # Prepare headers
            headers = {
                "Content-Type": "application/json",
                "x-vendor-id": self.avs_vendor_id,
                "Ocp-Apim-Subscription-Key": self.avs_subscription_key
            }

            # Build full URL
            full_url = f"{self.avs_base_url}/api/AddressVendor/receive-verification-response"

            print(f"[AVS] Submitting to: {full_url}")
            print(f"[AVS] Vendor ID: {self.avs_vendor_id}")
            print(f"[AVS] Subscription Key (first 8 chars): {self.avs_subscription_key[:8] if self.avs_subscription_key else 'NOT SET'}...")
            print(f"[AVS] Payload: {json.dumps(avs_payload, indent=2)[:1000]}")

            # Submit to AVS
            response = requests.post(
                full_url,
                headers=headers,
                json=avs_payload
            )

            print(f"[AVS] Response Status: {response.status_code}")
            print(f"[AVS] Response Body: {response.text[:500] if response.text else 'empty'}")

            if response.status_code == 200:
                try:
                    response_json = response.json()
                except:
                    response_json = {"raw": response.text}
                return {
                    "success": True,
                    "status_code": 200,
                    "message": "Verification result submitted successfully",
                    "response": response_json
                }
            else:
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "message": "Failed to submit verification result",
                    "error": response.text,
                    "url_called": full_url
                }

        except Exception as e:
            return {
                "success": False,
                "status_code": 500,
                "message": f"Exception submitting result: {str(e)}"
            }

    def _build_avs_response(self,
                           activity_id: str,
                           shipday_data: Dict,
                           verification_details: Dict) -> Dict:
        """
        Build AVS response object from Shipday data and verification details

        Args:
            activity_id: Original activity ID
            shipday_data: Data from Shipday
            verification_details: Verification details collected by field agent

        Returns:
            Formatted AVS response object
        """
        # Extract media from verification details
        address_media = []
        if 'photos' in verification_details:
            for photo in verification_details['photos']:
                address_media.append({
                    "fileName": photo.get('fileName', 'photo.jpg'),
                    "contentBase64": photo.get('base64Content', ''),
                    "contentType": photo.get('contentType', 'image/jpeg'),
                    "mediaType": MediaType.IMAGE.value,
                    "caption": photo.get('caption', 'Address verification photo'),
                    "takenAt": photo.get('timestamp', datetime.utcnow().isoformat() + 'Z'),
                    "latitude": photo.get('latitude', ''),
                    "longitude": photo.get('longitude', '')
                })

        # Parse customer name into first and last name
        customer_name = verification_details.get('customerName', '')
        first_name, last_name = self._parse_name(customer_name)

        # Parse address into components
        address = verification_details.get('address', '')
        address_parts = self._parse_address(address)

        # Build response object using camelCase
        response = {
            "activityId": activity_id,
            "customerName": customer_name,
            "customer_reference": activity_id,
            "subject_first_name": first_name,
            "subject_last_name": last_name,
            "address": address,
            "address_street": address_parts['street'],
            "address_city": address_parts['city'],
            "address_state": address_parts['state'],
            "address_country": address_parts['country'],
            "visitDate": verification_details.get('visitDate', datetime.utcnow().isoformat() + 'Z'),
            "addressExists": verification_details.get('addressExists', True),
            "isResidentialAddress": verification_details.get('isResidentialAddress', True),
            "isCustomerResidence": verification_details.get('isCustomerResidence', True),
            "isCustomerKnown": verification_details.get('isCustomerKnown', False),
            "relationshipWithPersonMet": verification_details.get('relationshipWithPersonMet', 'Not specified'),
            "nameOfPersonMet": verification_details.get('nameOfPersonMet', 'Name not given'),
            "easeOfLocation": verification_details.get('easeOfLocation', 'Medium'),
            "comments": verification_details.get('comments', ''),
            "additionalComments": verification_details.get('additionalComments', ''),
            "receivedDate": datetime.utcnow().isoformat() + 'Z',
            "metOthers": verification_details.get('metOthers', False),
            "verificationStatus": verification_details.get('verificationStatus', VerificationStatus.SUCCESS.value),
            "addressMedia": address_media,
            "reportUrl": verification_details.get('reportUrl', '')
        }

        return response

    @staticmethod
    def _parse_name(full_name: str) -> tuple:
        """
        Parse full name into first and last name

        Args:
            full_name: Full name string

        Returns:
            Tuple of (first_name, last_name)
        """
        if not full_name:
            return ("Unknown", "Unknown")

        parts = full_name.strip().split()
        if len(parts) == 0:
            return ("Unknown", "Unknown")
        elif len(parts) == 1:
            return (parts[0], parts[0])
        else:
            # First part is first name, rest is last name
            first_name = parts[0]
            last_name = ' '.join(parts[1:])
            return (first_name, last_name)

    @staticmethod
    def _parse_address(address: str) -> Dict[str, str]:
        """
        Parse address string into components

        Args:
            address: Full address string

        Returns:
            Dictionary with street, city, state, country
        """
        if not address:
            return {
                'street': 'Unknown',
                'city': 'Unknown',
                'state': 'Unknown',
                'country': 'Nigeria'
            }

        # Split address by commas
        parts = [p.strip() for p in address.split(',')]

        # Default values
        result = {
            'street': parts[0] if len(parts) > 0 else 'Unknown',
            'city': 'Unknown',
            'state': 'Unknown',
            'country': 'Nigeria'
        }

        # Try to extract city, state, country
        if len(parts) >= 2:
            result['city'] = parts[1] if parts[1] else 'Unknown'
        if len(parts) >= 3:
            result['state'] = parts[2] if parts[2] else 'Unknown'
        if len(parts) >= 4:
            result['country'] = parts[3] if parts[3] else 'Nigeria'

        # Common patterns for Nigerian addresses
        address_lower = address.lower()

        # Extract city from common patterns
        if 'lagos' in address_lower and result['city'] == 'Unknown':
            result['city'] = 'Lagos'
            result['state'] = 'Lagos'
        elif 'abuja' in address_lower and result['city'] == 'Unknown':
            result['city'] = 'Abuja'
            result['state'] = 'FCT'
        elif 'ikeja' in address_lower and result['city'] == 'Unknown':
            result['city'] = 'Ikeja'
            result['state'] = 'Lagos'
        elif 'port harcourt' in address_lower and result['city'] == 'Unknown':
            result['city'] = 'Port Harcourt'
            result['state'] = 'Rivers'

        # Always default to Nigeria if country not specified
        if 'nigeria' in address_lower or result['country'] == 'Unknown':
            result['country'] = 'Nigeria'

        return result

    # ==================== SHIPDAY API METHODS ====================

    def get_shipday_order(self, order_id: str) -> Optional[Dict]:
        """
        Get order details from Shipday

        Args:
            order_id: Shipday order ID

        Returns:
            Order details or None
        """
        try:
            headers = {
                "Authorization": f"Basic {self.shipday_api_key}",
                "Content-Type": "application/json"
            }

            response = requests.get(
                f"{self.shipday_base_url}/orders/{order_id}",
                headers=headers
            )

            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error fetching Shipday order: {response.status_code}")
                return None

        except Exception as e:
            print(f"Exception fetching Shipday order: {str(e)}")
            return None

    def list_completed_orders(self, start_date: str = None, end_date: str = None) -> List[Dict]:
        """
        List completed orders from Shipday within date range

        Args:
            start_date: Start date in ISO format
            end_date: End date in ISO format

        Returns:
            List of completed orders
        """
        try:
            headers = {
                "Authorization": f"Basic {self.shipday_api_key}",
                "Content-Type": "application/json"
            }

            params = {
                "status": "completed"
            }

            if start_date:
                params["startDate"] = start_date
            if end_date:
                params["endDate"] = end_date

            response = requests.get(
                f"{self.shipday_base_url}/orders",
                headers=headers,
                params=params
            )

            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error listing orders: {response.status_code}")
                return []

        except Exception as e:
            print(f"Exception listing orders: {str(e)}")
            return []

    # ==================== UTILITY METHODS ====================

    @staticmethod
    def encode_image_to_base64(image_path: str) -> str:
        """
        Encode image file to base64 string

        Args:
            image_path: Path to image file

        Returns:
            Base64 encoded string
        """
        try:
            with open(image_path, 'rb') as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
        except Exception as e:
            print(f"Error encoding image: {str(e)}")
            return ""

    @staticmethod
    def validate_avs_payload(payload: Dict) -> tuple[bool, List[str]]:
        """
        Validate AVS payload against requirements

        Args:
            payload: The payload to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Validate vendorId
        if not payload.get('vendorId'):
            errors.append("vendorId is required")

        # Validate addressVerificationResponses
        responses = payload.get('addressVerificationResponses', [])
        if not responses:
            errors.append("addressVerificationResponses must contain at least one record")

        for idx, response in enumerate(responses):
            prefix = f"Response[{idx}]"

            # Required fields
            required_fields = {
                'activityId': None,
                'customerName': 150,
                'address': 300,
                'visitDate': None,
                'addressExists': None,
                'isResidentialAddress': None,
                'isCustomerResidence': None,
                'isCustomerKnown': None,
                'relationshipWithPersonMet': 50,
                'nameOfPersonMet': 150,
                'easeOfLocation': 100,
                'receivedDate': None,
                'metOthers': None,
                'verificationStatus': None,
                'addressMedia': None,
                'reportUrl': None
            }

            for field, max_length in required_fields.items():
                if field not in response or response[field] is None:
                    errors.append(f"{prefix}.{field} is required")
                elif max_length and isinstance(response[field], str):
                    if len(response[field]) > max_length:
                        errors.append(f"{prefix}.{field} exceeds max length of {max_length}")

            # Validate addressMedia
            media = response.get('addressMedia', [])
            if not media:
                errors.append(f"{prefix}.addressMedia is required (regulatory requirement)")
            else:
                for media_idx, item in enumerate(media):
                    media_prefix = f"{prefix}.addressMedia[{media_idx}]"
                    if not item.get('fileName'):
                        errors.append(f"{media_prefix}.fileName is required")
                    if not item.get('contentBase64'):
                        errors.append(f"{media_prefix}.contentBase64 is required")
                    if not item.get('contentType'):
                        errors.append(f"{media_prefix}.contentType is required")
                    if not item.get('mediaType'):
                        errors.append(f"{media_prefix}.mediaType is required")

            # Validate reportUrl format
            report_url = response.get('reportUrl', '')
            if report_url and not (report_url.startswith('http://') or report_url.startswith('https://')):
                errors.append(f"{prefix}.reportUrl must be a valid absolute URL")

        return len(errors) == 0, errors
