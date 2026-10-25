"""Offline regression tests for pasted app-credential formatting."""
import unittest

from telegram_credentials import normalize_credential, parse_api_hash, parse_api_id


class CredentialNormalizationTests(unittest.TestCase):
    def test_boundary_whitespace_and_hidden_formatting_are_removed(self):
        value = '\ufeff\u200b \t12345\n\u2060\u200e'
        self.assertEqual(normalize_credential(value), '12345')
        self.assertEqual(parse_api_id(value), 12345)

    def test_one_matching_quote_pair_and_its_inner_boundaries_are_removed(self):
        for opening, closing in [("'", "'"), ('"', '"'), ('\u2018', '\u2019'), ('\u201c', '\u201d')]:
            with self.subTest(opening=opening):
                value = '\u200b ' + opening + '\ufeff 12345 \u2060' + closing + '\n'
                self.assertEqual(normalize_credential(value), '12345')
                self.assertEqual(parse_api_id(value), 12345)
                self.assertEqual(parse_api_hash(opening + 'Ab09' * 8 + closing), 'Ab09' * 8)

    def test_normalizer_preserves_interior_characters(self):
        for value in ['12\u200b345', '12 345', '12\n345', 'api_id=12345']:
            with self.subTest(value=value):
                self.assertEqual(normalize_credential(value), value)

    def test_at_most_one_quote_pair_is_unwrapped(self):
        self.assertEqual(normalize_credential("'\"12345\"'"), '"12345"')
        with self.assertRaisesRegex(ValueError, 'extra quotation'):
            parse_api_id("'\"12345\"'")

    def test_missing_values_normalize_to_empty_text(self):
        self.assertEqual(normalize_credential(None), '')
        self.assertEqual(normalize_credential('\u200b \n'), '')
        self.assertEqual(normalize_credential('" \ufeff "'), '')


class ApiCredentialParsingTests(unittest.TestCase):
    def test_positive_decimal_api_id_is_returned_as_integer(self):
        self.assertEqual(parse_api_id('0012345'), 12345)
        # Python int accepts Unicode decimal digits; normalization preserves them.
        self.assertEqual(parse_api_id('\uff11\uff12\uff13'), 123)
        self.assertEqual(parse_api_id('\u0661\u0662\u0663'), 123)

    def test_hash_case_is_preserved(self):
        self.assertEqual(parse_api_hash('AbCd0123' * 4), 'AbCd0123' * 4)

    def test_missing_and_zero_ids_are_rejected(self):
        for value in [None, '', ' ', '\ufeff', '0', '000']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_api_id(value)

    def test_ids_do_not_extract_digits_from_mixed_text(self):
        for value in ['TELEGRAM_API_ID=12345', 'api_id:12345', 'a12345', '12345b',
                      '12345.0', '-12345', '+12345', '12_345', '12,345', 'a' * 32,
                      '12\u200b345', '12 345', '12\n345', "'12345\"", '\u201d12345\u201c',
                      '\u201812345', '12345\u2019']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_api_id(value)

    def test_hashes_require_exactly_32_ascii_hexadecimal_characters(self):
        for value in [None, '', 'a' * 31, 'a' * 33, 'g' * 32, '1' * 16,
                      'TELEGRAM_API_HASH=' + 'a' * 32, 'a' * 15 + '\u200b' + 'a' * 17,
                      'a' * 15 + ' ' + 'a' * 17, 'a' * 15 + '\n' + 'a' * 17,
                      "'" + 'a' * 32 + '\u2019', '"\'' + 'a' * 32 + '\'"',
                      '\uff41' * 32]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_api_hash(value)

    def test_errors_describe_only_the_formatting_category(self):
        for parser, values in [
                (parse_api_id, {'secret-id=12345': 'assignment',
                                '123\u200b45': 'hidden formatting',
                                "'12345\"": 'quotation',
                                'private-api-id': 'non-decimal'}),
                (parse_api_hash, {'secret-hash=abcdef': 'assignment',
                                  'a' * 15 + '\u200b' + 'a' * 17: 'hidden formatting',
                                  "'" + 'a' * 32: 'quotation',
                                  'private-api-hash': 'non-hexadecimal',
                                  'AbCd' * 7: 'exactly 32'})]:
            for value, category in values.items():
                with self.subTest(parser=parser.__name__, category=category):
                    with self.assertRaises(ValueError) as raised:
                        parser(value)
                    error = str(raised.exception)
                    self.assertIn(category, error)
                    self.assertIn('In Render', error)
                    self.assertNotIn(value, error)
                    self.assertNotIn('\u200b', error)

    def test_unexpected_types_are_rejected_without_echoing_them(self):
        for parser in [normalize_credential, parse_api_id, parse_api_hash]:
            with self.subTest(parser=parser.__name__), self.assertRaises(ValueError) as raised:
                parser(b'private-credential')
            self.assertNotIn('private-credential', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
