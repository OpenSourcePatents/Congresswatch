"""Safety and exact-match unit controls; these are not retrieval-quality fixtures."""
from random import Random
from unittest import TestCase, main
from defusedxml.common import EntitiesForbidden
from fetch_bills.utils.official_passages import Passages, Tokens
from fetch_bills.utils.official_prose import parse, read_body


class OfficialProseTests(TestCase):
    def test_exact_suffix_matches_brute_for_repeated_tokens(self):
        rand = Random(981)
        for _ in range(120):
            a = [rand.choice('abcd') for _ in range(rand.randrange(1, 30))]
            b = [rand.choice('abcd') for _ in range(rand.randrange(1, 30))]
            expect = max((n for n in range(1, min(len(a),len(b)) + 1)
                          if {tuple(a[i:i+n]) for i in range(len(a)-n+1)} &
                             {tuple(b[j:j+n]) for j in range(len(b)-n+1)}),default=0)
            length, left, right = Passages(Tokens.from_text(' '.join(a))).longest(Tokens.from_text(' '.join(b)))
            self.assertEqual(length, expect)
            self.assertEqual(a[left:left+length], b[right:right+length])

    def test_legislative_form_identity_not_metadata_heading_template(self):
        raw = b"""<bill><metadata><title>109 HR 139 IH: old template</title></metadata>
          <form><congress>119th CONGRESS</congress><legis-num>H. R. 139</legis-num></form>
          <legis-body><section><header>Review</header><text>before <strike>deleted old language</strike>
          after</text><table-of-contents>exclude list</table-of-contents></section></legis-body></bill>"""
        x = read_body('BILLS-119hr139ih',raw)
        self.assertEqual(x.text, 'Review before after')
        with self.assertRaisesRegex(ValueError, 'XML identity mismatch'):
            read_body('BILLS-119hr300ih',raw)

    def test_external_entities_forbidden(self):
        with self.assertRaises(EntitiesForbidden):
            parse(b'<!DOCTYPE bill [<!ENTITY e SYSTEM "file:///nonexistent">]><bill>&e;</bill>')


if __name__ == '__main__':
    main()
