from harness.web_search.answer_rules import match_reference_answer, reference_rule


def test_explicit_alternative_without_annotation():
    ref = 'tramp steamer (or collier)'
    assert match_reference_answer(ref, 'The vessel resembled a collier.')
    assert match_reference_answer(ref, 'It resembled a tramp steamer.')
    assert not match_reference_answer(ref, 'The steamship had a collision.')


def test_numeric_range_checks_entire_number_and_bounds():
    ref = '150 (acceptable range: anything between 148 and 152)'
    for text in ('148 engravings', '150 engravings', '152 engravings'):
        assert match_reference_answer(ref, text)
    for text in ('147 engravings', '153 engravings', '1,150 engravings', '150.9.5', '1150', '-150', '150%'):
        assert not match_reference_answer(ref, text)


def test_units_and_percent_are_not_discarded():
    ref = '55% (acceptable range: anything between 54% and 56%)'
    assert match_reference_answer(ref, 'It was 55%.')
    assert not match_reference_answer(ref, 'There were 55 people.')
    length = '390 metres (acceptable range: anything between 386 and 394 metres)'
    assert match_reference_answer(length, 'The bridge spans 390 metres long')
    assert not match_reference_answer(length, 'It is 390 feet long')


def test_grouped_decimal_values_and_scale():
    ref = '1.65 million USD (acceptable range: anything between 1.6 million and 1.7 million USD)'
    assert match_reference_answer(ref, 'It cost 1.65 million USD in total')
    assert not match_reference_answer(ref, 'It cost 1.65 USD in total')
    assert not match_reference_answer(ref, 'It cost 1.75 million USD in total')
    ref = '10,990,502 (acceptable range: anything between 10,440,976 and 11,540,028)'
    assert match_reference_answer(ref, 'Population: 11,000,000 people')
    assert not match_reference_answer(ref, 'Population: 12,000,000 people')


def test_dimensions_and_fixed_duration_prefix():
    ref = '405 x 480 cm (acceptable range: [400-409] x [475-485] cm)'
    assert match_reference_answer(ref, 'The work measures 405 × 480 cm.')
    assert not match_reference_answer(ref, 'The work measures 480 × 405 cm.')
    assert not match_reference_answer(ref, 'The work measures 405 × 480 inches.')
    ref = '1 year, 161 days (acceptable range: 1 year, anything between 160 and 162 days)'
    assert match_reference_answer(ref, 'It lasted 1 year, 160 days in total.')
    assert not match_reference_answer(ref, 'It lasted 2 years, 160 days in total.')
    assert not match_reference_answer(ref, 'It lasted 160 days in total.')


def test_inline_alternative_preserves_rest_of_answer():
    ref = 'A, B, C♯, D, E, F (or E♯), F♯, G♯'
    assert match_reference_answer(ref, 'A, B, C♯, D, E, E♯, F♯, G♯')
    assert not match_reference_answer(ref, 'E♯')
    assert not match_reference_answer(ref, 'A, B, C, D, E, F, F, G')


def test_quoted_alias_and_unknown_parenthetical():
    ref = 'Second Empire (also know as "French Empire" or "2nd Empire/Italianate")'
    assert match_reference_answer(ref, 'It has a French Empire design.')
    assert not match_reference_answer(ref, 'It is a French design.')
    ref = "There's a hero (if you look inside your heart)"
    assert reference_rule(ref)['unsupported_annotation']
    assert not match_reference_answer(ref, "There's a hero")
    assert match_reference_answer(ref, ref)


def test_unknown_range_is_explicit_and_base_retained():
    ref = '150 (acceptable range: roughly nearby)'
    assert reference_rule(ref)['unsupported_annotation'] == 'roughly nearby'
    assert match_reference_answer(ref, '150 objects')
    assert not match_reference_answer(ref, '149 objects')


def test_literal_numeric_formatting_preserves_numeric_identity():
    assert match_reference_answer("120,000 euros", "Damages of 120000 euros.")
    assert not match_reference_answer("120,000 euros", "Damages of 120 euros.")
    assert not match_reference_answer("150", "1150")
    assert not match_reference_answer("1.5", "15")


def test_quantity_modifiers_remain_part_of_literal_and_ranged_units():
    for ref in ('390 m', '390 m (acceptable range: anything between 386 and 394 m)'):
        assert match_reference_answer(ref, 'The bridge is 390 m long.')
        for invalid in ('390 m/s', '390 m / s', '390 m per second', '390 m²', '390 m^2', '390 m2'):
            assert not match_reference_answer(ref, invalid), (ref, invalid)
    for ref in ('150', '150 (acceptable range: anything between 148 and 152)'):
        assert match_reference_answer(ref, '150 people')
        for invalid in ('150%', '150 %', '150‰', '150 per second', '150/second'):
            assert not match_reference_answer(ref, invalid), (ref, invalid)
    assert match_reference_answer('150%', 'It rose 150 %.')
    assert not match_reference_answer('150%', 'It rose 150 people.')


def test_squared_reference_retains_its_explicit_power():
    ref = '74,500 km2 (acceptable range: anything between 70,775 and 78,225 km2)'
    assert match_reference_answer(ref, 'The area is 74500 km².')
    assert not match_reference_answer(ref, 'The distance is 74500 km.')
    assert not match_reference_answer(ref, 'The rate is 74500 km²/s.')
