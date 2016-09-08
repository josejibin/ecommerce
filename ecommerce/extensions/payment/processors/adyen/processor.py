""" Adyen payment processing. """
from __future__ import unicode_literals

from datetime import datetime
import logging
from urlparse import urljoin

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from oscar.apps.payment.exceptions import GatewayError, TransactionDeclined
from oscar.core.loading import get_class, get_model
import requests

from ecommerce.core.constants import ISO_8601_FORMAT
from ecommerce.extensions.order.constants import PaymentEventTypeName
from ecommerce.extensions.payment.exceptions import NotificationParserError
from ecommerce.extensions.payment.helpers import sign
from ecommerce.extensions.payment.processors.base import BasePaymentProcessor
from ecommerce.extensions.payment.utils import minor_units
from ecommerce.extensions.refund.status import REFUND


logger = logging.getLogger(__name__)

Basket = get_model('basket', 'Basket')
BillingAddress = get_model('order', 'BillingAddress')
Country = get_model('address', 'Country')
OrderNumberGenerator = get_class('order.utils', 'OrderNumberGenerator')
PaymentEvent = get_model('order', 'PaymentEvent')
PaymentEventType = get_model('order', 'PaymentEventType')
PaymentProcessorResponse = get_model('payment', 'PaymentProcessorResponse')
ProductClass = get_model('catalogue', 'ProductClass')
Source = get_model('payment', 'Source')
SourceType = get_model('payment', 'SourceType')


class Adyen(BasePaymentProcessor):
    """
    Adyen CSE Integration (June 2016)

    For reference, see https://docs.adyen.com/developers
    """

    ACCEPTED_NOTIFICATION_RESPONSE = '[accepted]'
    BASKET_TEMPLATE = 'adyen/basket.html'
    CONFIGURATION_MODEL = 'ecommerce.extensions.payment.processors.adyen.models.AdyenConfiguration'
    EXPECTED_REFUND_REQUEST_RESPONSE = '[cancelOrRefund-received]'
    NAME = 'adyen'

    @property
    def generation_time(self):
        return datetime.utcnow().strftime(ISO_8601_FORMAT)

    def can_handle_notification(self, notification_data):
        try:
            self._parse_notification_items(notification_data)
        except NotificationParserError:
            return False

        return True

    def get_billing_address(self, payment_form_data):
        try:
            return BillingAddress(
                first_name=payment_form_data['first_name'],
                last_name=payment_form_data['last_name'],
                line1=payment_form_data['street_address'],
                line2=payment_form_data.get('apartment_number', ''),  # Address line 2 is optional
                line4=payment_form_data['city'],  # Oscar uses line4 for city
                postcode=payment_form_data['postal_code'],
                state=payment_form_data.get('state', ''),  # State is optional
                country=Country.objects.get(iso_3166_1_a2=payment_form_data['country'])
            )
        except KeyError:
            return None

    def get_transaction_parameters(self, basket, request=None):
        """
        Generate a dictionary of parameters Adyen requires to complete a transaction.

        Arguments:
            basket (Basket): The basket of products being purchase.; not used by this method.

        Keyword Arguments:
            request (Request): A Request object which could be used to construct an absolute URL; not
                used by this method.

        Returns:
            dict: Adyen-specific parameters required to complete a transaction.
        """
        parameters = {
            'payment_page_url': '',
        }

        return parameters

    def handle_payment_authorization_response(self, response, basket):
        transaction_id = response['pspReference']
        result_code = response['resultCode'].lower()

        if result_code != 'authorised':
            raise TransactionDeclined

        return self._process_authorization(transaction_id, basket)

    def issue_credit(self, source, amount, currency):
        order = source.order

        response = requests.post(
            urljoin(self.configuration.payment_api_url, 'cancelOrRefund'),
            auth=(self.configuration.web_service_username, self.configuration.web_service_password),
            headers={
                'Content-Type': 'application/json'
            },
            json={
                'merchantAccount': self.configuration.merchant_account_code,
                'originalReference': source.reference,
                'reference': order.number
            }
        )

        if response.status_code != requests.codes.OK:
            logger.error(
                'Adyen refund request failed with status [%d] for basket [%s].',
                response.status_code,
                order.number
            )
            raise GatewayError

        adyen_response = response.json()
        transaction_id = adyen_response.get('pspReference')
        refund_response = adyen_response.get('response')
        payment_processor_response = self.record_processor_response(adyen_response, transaction_id)

        logger.info(
            'Received Adyen refund request response with refund response [%s] for transaction [%s], '
            'associated with basket [%s]. '
            'The payment processor response was recorded in record [%d].',
            refund_response,
            transaction_id,
            order.number,
            payment_processor_response.id
        )

        if refund_response != self.EXPECTED_REFUND_REQUEST_RESPONSE:
            raise GatewayError

        return self.CREDIT_PENDING

    def process_notification(self, notification_data):
        """
        Handle notification/response from Adyen.
        """
        try:
            notification_items = self._parse_notification_items(notification_data)
        except NotificationParserError:
            payment_processor_response = self.record_processor_response(notification_data)
            logger.exception(
                'Received invalid Adyen notification. '
                'The payment processor response was recorded in record [%d].',
                payment_processor_response.id
            )

        for notification_item in notification_items:
            transaction_id = None
            try:
                notification = notification_item['NotificationRequestItem']
                transaction_id = notification['pspReference']
                order_number = notification['merchantReference']
            except KeyError:
                payment_processor_response = self.record_processor_response(notification_item, transaction_id)
                logger.exception(
                    'Received invalid Adyen notification for transaction [%s].'
                    'The payment processor response was recorded in record [%d].',
                    transaction_id,
                    payment_processor_response.id
                )
                continue

            if not self._is_signature_valid(notification):
                payment_processor_response = self.record_processor_response(notification, transaction_id)
                logger.error(
                    'Adyen notification HMAC signature verification failed for transaction [%s].'
                    'The payment processor response was recorded in record [%d].',
                    transaction_id,
                    payment_processor_response.id
                )
                continue

            try:
                basket_id = OrderNumberGenerator().basket_id(order_number)
                basket = Basket.objects.get(id=int(basket_id))
            except IndexError:
                payment_processor_response = self.record_processor_response(notification, transaction_id)
                logger.exception(
                    'Received Adyen notification for transaction [%s], associated with unknown order [%s].'
                    'The payment processor response was recorded in record [%d].',
                    transaction_id,
                    order_number,
                    payment_processor_response.id
                )
                continue
            except ObjectDoesNotExist:
                payment_processor_response = self.record_processor_response(notification, transaction_id)
                logger.exception(
                    'Received Adyen notification for transaction [%s], associated with unknown basket [%s].'
                    'The payment processor response was recorded in record [%d].',
                    transaction_id,
                    basket_id,
                    payment_processor_response.id
                )
                continue

            payment_processor_response = self.record_processor_response(notification, transaction_id, basket)
            logger.info(
                'Received Adyen notification for transaction [%s], associated with basket [%s].'
                'The payment processor response was recorded in record [%d].',
                transaction_id,
                basket_id,
                payment_processor_response.id
            )

            # Explicitly delimit operations which will be rolled back if an exception occurs.
            with transaction.atomic():
                try:
                    event_code = notification['eventCode']
                    return getattr(
                        self,
                        '_handle_{event}'.format(event=event_code.lower())
                    )(transaction_id, notification, basket)
                except KeyError:
                    logger.error(
                        'Received Adyen notification with missing eventCode for transaction [%s], '
                        'associated with basket [%s].'
                        'The payment processor response was recorded in record [%d].',
                        transaction_id,
                        basket.order_number,
                        payment_processor_response.id
                    )
                    continue
                except AttributeError:
                    logger.error(
                        'Received Adyen notification with unsupported Adyen eventCode [%s] '
                        'for transaction [%s], associated with basket [%s].'
                        'The payment processor response was recorded in record [%d].',
                        event_code,
                        transaction_id,
                        basket.order_number,
                        payment_processor_response.id
                    )
                    continue

    def send_payment_authorization_request(self, basket, authorization_data):
        """
        Send authorise API request to Adyen to authorize payment.
        """
        request_url = urljoin(self.configuration.payment_api_url, 'authorise')
        request_payload = {
            'additionalData': {
                'card.encrypted.json': authorization_data['adyen-encrypted-data']
            },
            'amount': {
                'value': minor_units(basket.total_incl_tax, basket.currency),
                'currency': basket.currency
            },
            'reference': basket.order_number,
            'merchantAccount': self.configuration.merchant_account_code
        }

        # Add additional shopper data collected on payment form
        request_payload.update(self._get_shopper_data(**authorization_data))

        response = requests.post(
            request_url,
            auth=(self.configuration.web_service_username, self.configuration.web_service_password),
            headers={
                'Content-Type': 'application/json'
            },
            json=request_payload
        )

        if response.status_code != requests.codes.OK:
            logger.error(
                'Adyen payment authorization failed with status [%d] for basket [%s].',
                response.status_code,
                basket.order_number
            )
            raise GatewayError

        adyen_response = response.json()
        transaction_id = adyen_response.get('pspReference')
        result_code = adyen_response.get('resultCode')
        payment_processor_response = self.record_processor_response(adyen_response, transaction_id, basket)

        logger.info(
            'Received Adyen payment authorization response with result code [%s] for transaction [%s], '
            'associated with basket [%s]. '
            'The payment processor response was recorded in record [%d].',
            result_code,
            transaction_id,
            basket.order_number,
            payment_processor_response.id
        )

        return payment_processor_response

    def _generate_signature(self, notification):
        secret = self.configuration.notifications_hmac_key.decode('hex')
        amount = notification.get('amount', {})
        signed_values = [
            notification.get('pspReference', ''),
            notification.get('originalReference', ''),
            notification.get('merchantAccountCode', ''),
            notification.get('merchantReference', ''),
            str(amount.get('value', '')),
            amount.get('currency', ''),
            notification.get('eventCode', ''),
            str(notification.get('success', '')).lower()
        ]
        message = ':'.join(signed_values)

        return sign(message, secret)

    def _get_shopper_data(self, **kwargs):
        return {
            'shopper_name': {
                'firstName': kwargs.get('first_name', ''),
                'lastName': kwargs.get('last_name', '')
            },
            'shopperEmail': kwargs.get('email', ''),
            'billingAddress': {
                'street': kwargs.get('street_address', ''),
                'houseNumberOrName': kwargs.get('apartment_number', ''),
                'city': kwargs.get('city', ''),
                'stateOrProvince': kwargs.get('state', ''),
                'postalCode': kwargs.get('postal_code', ''),
                'country': kwargs.get('country', '')
            },
            'shopperIP': kwargs.get('ip', '')
        }

    def _handle_authorisation(self, transaction_id, notification, basket):
        pass

    def _handle_cancel_or_refund(self, transaction_id, notification, basket):
        self._handle_refund(transaction_id, notification, basket)

    def _handle_refund(self, transaction_id, notification, basket):
        order = basket.order_set.first()
        # TODO Update this if we ever support multiple payment sources for a single order.
        source = order.sources.first()

        try:
            refund = order.refunds.get(status__in=[REFUND.PENDING_WITH_REVOCATION, REFUND.PENDING_WITHOUT_REVOCATION])
        except ObjectDoesNotExist:
            # The order does not have a Refund associated with it, so the refund must have been
            # initiated from the Adyen admin portal and not Otto.
            logger.exception(
                'Received Adyen refund notification for order [%s], but could not find a matching Refund.',
                order.number
            )
            return

        if notification.get('success') == 'true':
            amount = refund.total_credit_excl_tax
            source.refund(amount, reference=transaction_id)
            revoke_fulfillment = refund.status == REFUND.PENDING_WITH_REVOCATION
            refund.set_status(REFUND.PAYMENT_REFUNDED)
            refund.complete(revoke_fulfillment)
            event_type, __ = PaymentEventType.objects.get_or_create(name=PaymentEventTypeName.REFUNDED)
            PaymentEvent.objects.create(
                event_type=event_type,
                order=order,
                amount=amount,
                reference=transaction_id,
                processor_name=self.NAME
            )
        else:
            logger.error('Adyen refund request failed for order [%s]', order.number)
            refund.set_status(REFUND.PAYMENT_REFUND_ERROR)

    def _is_signature_valid(self, notification):
        try:
            return self._generate_signature(notification) == notification['additionalData']['hmacSignature']
        except KeyError:
            logger.exception('Invalid Adyen HMAC signature')
            return False

    def _parse_notification_items(self, notification_data):
        try:
            return notification_data['notificationItems']
        except (KeyError, TypeError, ValueError):
            raise NotificationParserError

    def _process_authorization(self, transaction_id, basket):
        # Create Source to track all transactions related to this processor and order
        source_type, __ = SourceType.objects.get_or_create(name=self.NAME)
        currency = basket.currency
        total = basket.total_incl_tax

        source = Source(
            source_type=source_type,
            currency=currency,
            amount_allocated=total,
            amount_debited=total,
            reference=transaction_id
        )

        # Create PaymentEvent to track
        event_type, __ = PaymentEventType.objects.get_or_create(name=PaymentEventTypeName.PAID)
        event = PaymentEvent(event_type=event_type, amount=total, reference=transaction_id, processor_name=self.NAME)

        return source, event