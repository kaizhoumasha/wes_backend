from src.app.api_auth.services.app_service import api_app_service
from src.app.demo.services.demo_product_service import demo_product_service
from src.app.device.services.device_service import device_service
from src.app.workline.services.workline_service import workline_service
from src.common.cache_config import cache_settings


def test_device_service_uses_explicit_list_cache_config() -> None:
    assert device_service.cache_prefix == cache_settings.DEVICE.prefix
    assert device_service.cache_expire == cache_settings.DEVICE.expire
    assert device_service.list_cache_prefix == cache_settings.DEVICE_LIST.prefix
    assert device_service.list_cache_expire == cache_settings.DEVICE_LIST.expire


def test_workline_service_uses_explicit_list_cache_config() -> None:
    assert workline_service.cache_prefix == cache_settings.WORKLINE.prefix
    assert workline_service.cache_expire == cache_settings.WORKLINE.expire
    assert workline_service.list_cache_prefix == cache_settings.WORKLINE_LIST.prefix
    assert workline_service.list_cache_expire == cache_settings.WORKLINE_LIST.expire


def test_demo_product_service_uses_explicit_list_cache_config() -> None:
    assert demo_product_service.cache_prefix == cache_settings.DEMO_PRODUCT.prefix
    assert demo_product_service.cache_expire == cache_settings.DEMO_PRODUCT.expire
    assert demo_product_service.list_cache_prefix == cache_settings.DEMO_PRODUCT_LIST.prefix
    assert demo_product_service.list_cache_expire == cache_settings.DEMO_PRODUCT_LIST.expire


def test_api_app_service_uses_explicit_list_cache_config() -> None:
    assert api_app_service.cache_prefix == cache_settings.API_APP.prefix
    assert api_app_service.cache_expire == cache_settings.API_APP.expire
    assert api_app_service.list_cache_prefix == cache_settings.API_APP_LIST.prefix
    assert api_app_service.list_cache_expire == cache_settings.API_APP_LIST.expire
