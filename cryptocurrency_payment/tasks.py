import logging

from cryptocurrency_payment.models import CryptoCurrencyPayment
from django.utils import timezone
from django.db.models import Q
from cryptocurrency_payment.models import create_child_payment
from datetime import timedelta
from cryptocurrency_payment.app_settings import get_active_backends, get_backend_config, get_backend_obj

logger = logging.getLogger(__name__)

def update_payment_status():
    """
    Run this as a task periodically to check update for new or waiting payment and current processing payment on the blockchain
    :return:
    """
    backends = get_active_backends()
    for backend in backends:
        crypto_task = CryptoCurrencyPaymentTask(backend)
        crypto_task.update_crypto_currency_payment_status()


def cancel_unpaid_payment():
    """
    Run this as a task to cancel payment that have stayed in new or waiting for too long
    :return:
    """
    backends = get_active_backends()
    for backend in backends:
        crypto_task = CryptoCurrencyPaymentTask(backend)
        crypto_task.cancel_unpaid_payment()


def refresh_payment_prices():
    """
    Payment prices can be renewed periodically according to the latest conversion rate using this method
    :return:
    """
    backends = get_active_backends()
    for backend in backends:
        crypto_task = CryptoCurrencyPaymentTask(backend)
        crypto_task.refresh_new_crypto_payment_amount()


class CryptoCurrencyPaymentTask:
    """
    Implements task for a particular crypto backend . You can cancel a unpaid payment,
    Update unpaid payment status and refresh unpaid payment prices
    """

    def __init__(self, crypto):

        self.unpaid_payment_hrs = get_backend_config(
            crypto, "CANCEL_UNPAID_PAYMENT_HRS"
        )
        self.crypto = crypto
        self.backend_obj = get_backend_obj(crypto)
        self.ignore_underpayment_amount = get_backend_config(
            crypto, "IGNORE_UNDERPAYMENT_AMOUNT"
        )
        self.create_new_underpayment = get_backend_config(
            crypto, "CREATE_NEW_UNDERPAID_PAYMENT"
        )
        self.refresh_prices_every_mins = get_backend_config(
            crypto, "REFRESH_PRICE_AFTER_MINUTE"
        )
        self.confirmation_number = get_backend_config(
            crypto, "BALANCE_CONFIRMATION_NUM"
        )
        self.confirm_bal_without_hash_mins = get_backend_config(
            crypto, "IGNORE_CONFIRMED_BALANCE_WITHOUT_SAVED_HASH_MINS"
        )
        # TODO add throttling to prevent overloading the backend

    def update_crypto_currency_payment_status(self):
        """
        Get all payment that are in new status or processing status and check their status on
        the blockchain for confirmation. Only payment that are still in this particular status
        are checked

        :return:
        """
        yesterday_time = timezone.now() - timedelta(hours=self.unpaid_payment_hrs)
        payments = CryptoCurrencyPayment.objects.filter(
            crypto=self.crypto
        ).filter(
            Q(
                status__in=[
                    CryptoCurrencyPayment.PAYMENT_NEW,
                    CryptoCurrencyPayment.PAYMENT_PROCESSING,
                    CryptoCurrencyPayment.PAYMENT_WAIT
                ],
                created_at__gte=yesterday_time
            ) | Q(
                status=CryptoCurrencyPayment.PAYMENT_PROCESSING, tx_hash__isnull=False
            ),
        ).order_by('tx_hash').all()
        for payment in payments:
            try:
                status, value = self.backend_obj.confirm_address_payment(
                    address=payment.address,
                    total_crypto_amount=float(payment.crypto_amount),
                    confirmation_number=self.confirmation_number,
                    accept_confirmed_bal_without_hash_mins=self.confirm_bal_without_hash_mins,
                    tx_hash=payment.tx_hash,
                )
                if status == self.backend_obj.UNCONFIRMED_ADDRESS_BALANCE:
                    payment.status = payment.PAYMENT_PROCESSING
                    payment.tx_hash = value
                elif status == self.backend_obj.CONFIRMED_ADDRESS_BALANCE:
                    payment.status = payment.PAYMENT_PAID
                    payment.paid_crypto_amount = value
                elif status == self.backend_obj.UNDERPAID_ADDRESS_BALANCE:
                    remaining_value = value
                    remaining_fiat_value = self.backend_obj.convert_to_fiat(
                        remaining_value, payment.fiat_currency
                    )
                    # update paid amount and create new payment if necessary
                    payment.paid_crypto_amount = float(payment.crypto_amount) - remaining_value
                    if (
                        self.create_new_underpayment
                        and remaining_fiat_value > self.ignore_underpayment_amount
                    ):
                        payment.child_payment = create_child_payment(payment, remaining_fiat_value)
                        # mark the payment as paid
                        payment.status = payment.PAYMENT_PAID
                    elif remaining_fiat_value <= self.ignore_underpayment_amount:
                        payment.status = payment.PAYMENT_PAID
                    # TODO what is to do with the remaining amount ???? if it is not ignored or a new payment is not created
                    #else:
                elif status == self.backend_obj.NO_HASH_ADDRESS_BALANCE:
                    payment.status = payment.PAYMENT_WAIT
                    payment.tx_hash = None
                    payment.save() #no payment found yet
                else:
                    # unknown error occured cancel payment
                    payment.status = payment.PAYMENT_CANCELLED
                payment.save()
            except Exception as exc:
                logger.exception(f"Unable to process payment status update for payment #{payment.pk}")

    def cancel_unpaid_payment(self):
        """
        Any unpaid payment still in new payment status less than a particular time can be cancelled
        . To reduce resources when checking for new payment status
        :return:
        """
        yesterday_time = timezone.now() - timedelta(hours=self.unpaid_payment_hrs)
        payments = CryptoCurrencyPayment.objects.filter(
            status__in=[CryptoCurrencyPayment.PAYMENT_NEW, CryptoCurrencyPayment.PAYMENT_WAIT],
            created_at__lte=yesterday_time,
        )
        for payment in payments:
            payment.status = payment.PAYMENT_CANCELLED
            payment.save()

    def refresh_new_crypto_payment_amount(self):
        """
        Due to volatility of crypto prices, Payment prices can be refreshed regularly especially for payment in
        new status
        :return:
        """
        leastupdate_time = timezone.now() - timedelta(
            minutes=self.refresh_prices_every_mins
        )
        payments = CryptoCurrencyPayment.objects.filter(
            status__in=[CryptoCurrencyPayment.PAYMENT_NEW, CryptoCurrencyPayment.PAYMENT_WAIT], updated_at__lte=leastupdate_time
        )
        for payment in payments:
            amount = self.backend_obj.convert_from_fiat(
                payment.fiat_amount, payment.fiat_currency
            )
            payment.crypto_amount = amount
            payment.save()
