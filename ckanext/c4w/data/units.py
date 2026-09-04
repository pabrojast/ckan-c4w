# encoding: utf-8
"""Unit normalisation.

Ported from the GEMS explorer's download script, where three things about
the source forced the rules:

1. units are mixed INSIDE one parameter (arsenic comes in mg/l and ug/l, a
   factor of a thousand apart), so everything mass-per-volume is taken to
   ug/L and the display unit is chosen only after aggregation;
2. ``ug/g`` is a sediment concentration, not water. Mixing it with ug/L
   would be an error of an arbitrary factor, so it is rejected by name;
3. anything else is kept as written. A citizen dataset may legitimately
   carry NTU, cfu/100 mL or a Secchi depth, and none of those convert.
"""

# Mass per volume: everything to ug/L.
MASS_PER_VOLUME = {
    'mg/l': 1000.0,
    'µg/l': 1.0,
    'ug/l': 1.0,
    'μg/l': 1.0,          # Greek mu, as some exports write it
    'ng/l': 0.001,
    'g/l': 1000000.0,
}

# Kept as they come; only the spelling is tidied for display.
PASSTHROUGH = {
    'µs/cm': u'µS/cm',
    'us/cm': u'µS/cm',
    'μs/cm': u'µS/cm',
    'ms/cm': u'mS/cm',
    '%': u'%',
    '1/100 ml': u'1/100 mL',
    'cfu/100ml': u'cfu/100 mL',
    'cfu/100 ml': u'cfu/100 mL',
    'mpn/100ml': u'MPN/100 mL',
    'mpn/100 ml': u'MPN/100 mL',
    'm³/s': u'm³/s',
    'm3/s': u'm³/s',
    'l/s': u'L/s',
    'ph units': u'pH',
    'ph': u'pH',
    '°c': u'°C',
    'c': u'°C',
    'deg c': u'°C',
    'ntu': u'NTU',
    'fnu': u'FNU',
    'cm': u'cm',
    'm': u'm',
    'mm': u'mm',
}

# Sediment / dry-weight units: not water, rejected outright.
REJECTED_UNITS = {'µg/g', 'ug/g', 'μg/g', 'mg/g', 'mg/kg', 'µg/kg', 'ug/kg'}

CANONICAL_MASS = u'µg/L'


def canonical(unit):
    """``(canonical_unit, factor, reason)`` for a unit as written.

    ``reason`` is None when the unit is usable, else one of ``'empty'``,
    ``'sediment_unit'``. An unknown unit is NOT a rejection: it is returned
    as written with factor 1 -- consistency within a parameter is checked
    by the caller, not here.
    """
    raw = (unit or u'').strip()
    if not raw:
        return None, None, 'empty'
    key = raw.lower()
    if key in REJECTED_UNITS:
        return None, None, 'sediment_unit'
    factor = MASS_PER_VOLUME.get(key)
    if factor is not None:
        return CANONICAL_MASS, factor, None
    tidy = PASSTHROUGH.get(key)
    if tidy is not None:
        return tidy, 1.0, None
    return raw, 1.0, None


def is_mass_per_volume(unit):
    return (unit or u'').strip().lower() in MASS_PER_VOLUME


def display_unit(unit, median):
    """Unit to show and the scale to apply to the ug/L values.

    Decided after aggregation because it depends on the typical magnitude:
    a parameter whose median sits at thousands of ug/L reads better in mg/L.
    """
    if unit == CANONICAL_MASS and median is not None and median >= 1000:
        return u'mg/L', 0.001
    return unit, 1.0
