from urllib.parse import quote

from hypothesis import given
from hypothesis import strategies as st


def test_language_pages_resolve_database_languages(client):
    @given(
        language=st.sampled_from(["English", "Spanish", "French"]),
        uppercase=st.booleans(),
    )
    def property_test(language, uppercase):
        response = client.get(f"/language/{quote(language.upper() if uppercase else language)}")
        bias_response = client.get(
            f"/language/{quote(language.upper() if uppercase else language)}/bias"
        )

        assert response.status_code == 200
        assert bias_response.status_code == 200

    property_test()


def test_unknown_language_is_not_found(client):
    @given(name=st.text(alphabet="0123456789", min_size=1, max_size=20))
    def property_test(name):
        response = client.get(f"/language/{quote(name)}")

        assert response.status_code == 404

    property_test()


def test_language_is_a_user_bias_category(client):
    @given(uppercase=st.booleans())
    def property_test(uppercase):
        username = "ALICE" if uppercase else "alice"
        response = client.get(f"/user/{username}/bias?type=language")

        assert response.status_code == 200

    property_test()
