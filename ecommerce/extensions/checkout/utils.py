import logging

from edx_rest_api_client.client import EdxRestApiClient
from requests.exceptions import ConnectionError, Timeout
from slumber.exceptions import SlumberHttpBaseException


logger = logging.getLogger(__name__)


def get_provider_data(access_token, provider_id, site_configuration):
    """Get the provider information for provider id provider.

    Args:
        access_token(str): JWT access token
        provider_id(str): Identifier for the provider
        site_configuration(SiteConfiguration): Ecommerce Site Configuration

    Returns: dict
    """
    try:
        return EdxRestApiClient(
            site_configuration.build_lms_url('api/credit/v1/'),
            oauth_access_token=access_token
        ).providers(provider_id).get()
    except (ConnectionError, SlumberHttpBaseException, Timeout) as ex:
        logger.exception(
            'Failed to retrieve credit provider details for provider [%s], Because of [%s]',
            provider_id,
            ex,
        )
        return None
