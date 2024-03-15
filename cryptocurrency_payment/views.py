# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from urllib.parse import quote as urlencode
from django.views.generic import DetailView
from django.http import Http404
from cryptocurrency_payment.models import CryptoCurrencyPayment
from cryptocurrency_payment.app_settings import get_backend_config, get_backend_obj


class CryptoPaymentDetailView(DetailView):
    queryset = CryptoCurrencyPayment.objects.all()
    context_object_name = 'payment'
    template_name = 'cryptocurrency_payment/payment_detail.html'

    def get_object(self, *args ):

        obj = super(CryptoPaymentDetailView, self).get_object( *args )
        allow_anon_payment = get_backend_config(obj.crypto, key='ALLOW_ANONYMOUS_PAYMENT')
        if  allow_anon_payment is not True and   self.request.user.is_authenticated is not True:
            raise Http404
        elif not self.request.user.is_superuser and  obj.user and self.request.user != obj.user:
            raise Http404
        return obj

    def get_context_data(self, **kwargs):
        
        context = super(CryptoPaymentDetailView, self).get_context_data(**kwargs)
        backend_obj = get_backend_obj(self.object.crypto)
        
        # added address validity
        unpaid_payment_hrs = get_backend_config(self.object.crypto, key='CANCEL_UNPAID_PAYMENT_HRS')
        address_validity = self.object.created_at + timedelta(hours=unpaid_payment_hrs)
        context['address_validity'] = address_validity
        
        # misc config variables
        misc_config = {
            "logo_url": get_backend_config(self.object.crypto, key='CRYPTO_LOGO_URL'),
            "explorer_url": get_backend_config(self.object.crypto, key='EXPLORER_URL').format(tx_hash=self.object.tx_hash),
            "payment_uri": backend_obj.create_payment_uri(self.object.address, self.object.crypto_amount),
        }
        context['backend_config'] = misc_config
        
        return context
