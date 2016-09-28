import ddt
import httpretty
import mock
import requests
from requests import ConnectionError, Timeout

from ecommerce.extensions.checkout.utils import get_credit_provider_details
from ecommerce.tests.testcases import TestCase


@ddt.ddt
class UtilTests(TestCase):
    @httpretty.activate
    def test_get_credit_provider_details(self):
        """
        Check if correct data returns on the full filled request.
        """
        httpretty.register_uri(
            httpretty.GET,
            self.site.siteconfiguration.build_lms_url('api/credit/v1/providers/ASU/'),
            body='{"display_name": "Arizona State University"}',
            content_type="application/json"
        )
        provider_data = get_credit_provider_details(self.access_token, 'ASU', self.site.siteconfiguration)
        self.assertDictEqual(provider_data, {"display_name": "Arizona State University"})

    @httpretty.activate
    def test_get_credit_provider_details_unavailable_request(self):
        """
        Check if None return on the bad request
        """
        httpretty.register_uri(
            httpretty.GET,
            self.site.siteconfiguration.build_lms_url('api/credit/v1/providers/ABC/'),
            status=400
        )
        provider_data = get_credit_provider_details(self.access_token, 'ABC', self.site.siteconfiguration)
        self.assertEqual(provider_data, None)

    @ddt.data(ConnectionError, Timeout)
    def test_exceptions(self, exception):
        """ Verify the function returns None when a request exception is raised. """
        with mock.patch.object(requests, 'get', mock.Mock(side_effect=exception)):
            self.assertIsNone(get_credit_provider_details(self.access_token, 'ABC', self.site.siteconfiguration))
