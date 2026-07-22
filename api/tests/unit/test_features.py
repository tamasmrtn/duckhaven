from api import features


def test_feature_slugs_unique_and_lowercase():
    assert len(features.FEATURES) == len(set(features.FEATURES))
    for slug in features.FEATURES:
        assert slug == slug.lower()
        assert slug.replace("_", "").isalnum()


def test_api_version_is_positive_int():
    assert isinstance(features.API_VERSION, int)
    assert features.API_VERSION >= 1
