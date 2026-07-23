import taroai.config as config


_load_settings = config.load_settings


def _load_test_settings(env_file=".env"):
    return _load_settings(None if env_file == ".env" else env_file)


config.load_settings = _load_test_settings
