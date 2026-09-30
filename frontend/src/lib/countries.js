/**
 * Countries, in every language, without a translation file.
 *
 * The signup form used to offer eleven hardcoded countries, which is a policy
 * decision nobody made: it silently prevented players from everywhere else from
 * signing up at all.
 *
 * The list here is the full ISO-3166-1 alpha-2 set - just the codes, which are
 * stable, language-neutral and short. The *names* come from
 * `Intl.DisplayNames`, which every current browser ships with data for. That
 * means a player in Tokyo sees 日本 for JP and a player in Cairo sees اليابان,
 * with no translation work and no bundle to keep in sync.
 *
 * Two things this deliberately does not do:
 *
 *  * It does not sort alphabetically by English. Ordering is done against the
 *    *localised* name, so the list is in a sensible order in every language.
 *  * It does not encode which countries the operator accepts. That is the
 *    jurisdiction policy (`/api/config.jurisdiction`), which is server-side and
 *    changeable without a deploy - the form must not carry a second copy of it.
 */

// ISO 3166-1 alpha-2. Includes territories that issue their own documents and
// are commonly offered separately in a country picker.
export const ISO_COUNTRIES = [
  'AD', 'AE', 'AF', 'AG', 'AI', 'AL', 'AM', 'AO', 'AQ', 'AR', 'AS', 'AT', 'AU',
  'AW', 'AX', 'AZ', 'BA', 'BB', 'BD', 'BE', 'BF', 'BG', 'BH', 'BI', 'BJ', 'BL',
  'BM', 'BN', 'BO', 'BQ', 'BR', 'BS', 'BT', 'BV', 'BW', 'BY', 'BZ', 'CA', 'CC',
  'CD', 'CF', 'CG', 'CH', 'CI', 'CK', 'CL', 'CM', 'CN', 'CO', 'CR', 'CU', 'CV',
  'CW', 'CX', 'CY', 'CZ', 'DE', 'DJ', 'DK', 'DM', 'DO', 'DZ', 'EC', 'EE', 'EG',
  'EH', 'ER', 'ES', 'ET', 'FI', 'FJ', 'FK', 'FM', 'FO', 'FR', 'GA', 'GB', 'GD',
  'GE', 'GF', 'GG', 'GH', 'GI', 'GL', 'GM', 'GN', 'GP', 'GQ', 'GR', 'GS', 'GT',
  'GU', 'GW', 'GY', 'HK', 'HM', 'HN', 'HR', 'HT', 'HU', 'ID', 'IE', 'IL', 'IM',
  'IN', 'IO', 'IQ', 'IR', 'IS', 'IT', 'JE', 'JM', 'JO', 'JP', 'KE', 'KG', 'KH',
  'KI', 'KM', 'KN', 'KP', 'KR', 'KW', 'KY', 'KZ', 'LA', 'LB', 'LC', 'LI', 'LK',
  'LR', 'LS', 'LT', 'LU', 'LV', 'LY', 'MA', 'MC', 'MD', 'ME', 'MF', 'MG', 'MH',
  'MK', 'ML', 'MM', 'MN', 'MO', 'MP', 'MQ', 'MR', 'MS', 'MT', 'MU', 'MV', 'MW',
  'MX', 'MY', 'MZ', 'NA', 'NC', 'NE', 'NF', 'NG', 'NI', 'NL', 'NO', 'NP', 'NR',
  'NU', 'NZ', 'OM', 'PA', 'PE', 'PF', 'PG', 'PH', 'PK', 'PL', 'PM', 'PN', 'PR',
  'PS', 'PT', 'PW', 'PY', 'QA', 'RE', 'RO', 'RS', 'RU', 'RW', 'SA', 'SB', 'SC',
  'SD', 'SE', 'SG', 'SH', 'SI', 'SJ', 'SK', 'SL', 'SM', 'SN', 'SO', 'SR', 'SS',
  'ST', 'SV', 'SX', 'SY', 'SZ', 'TC', 'TD', 'TF', 'TG', 'TH', 'TJ', 'TK', 'TL',
  'TM', 'TN', 'TO', 'TR', 'TT', 'TV', 'TW', 'TZ', 'UA', 'UG', 'UM', 'US', 'UY',
  'UZ', 'VA', 'VC', 'VE', 'VG', 'VI', 'VN', 'VU', 'WF', 'WS', 'YE', 'YT', 'ZA',
  'ZM', 'ZW',
]

const displayNamesCache = new Map()

function displayNames(locale) {
  const key = String(locale || 'en').split('-')[0]
  if (!displayNamesCache.has(key)) {
    let names = null
    try {
      names = new Intl.DisplayNames([key], { type: 'region' })
    } catch {
      // Older engines: the raw code is a worse answer, not a broken one.
      names = null
    }
    displayNamesCache.set(key, names)
  }
  return displayNamesCache.get(key)
}

/** The country's name in the given language, falling back to the code. */
export function countryName(code, locale) {
  const names = displayNames(locale)
  try {
    return names?.of(code) || code
  } catch {
    return code
  }
}

/**
 * The whole list, named and sorted in the player's language.
 * `priority` codes (the operator's home market, a player's current country)
 * are floated to the top where every picker expects them.
 */
export function countryOptions(locale, priority = []) {
  const head = priority.filter((code) => code && ISO_COUNTRIES.includes(code))
  const options = ISO_COUNTRIES.map((code) => ({ code, name: countryName(code, locale) }))
  options.sort((a, b) => a.name.localeCompare(b.name, locale || 'en'))
  if (!head.length) return options

  const headSet = new Set(head)
  const headOptions = head.map((code) => ({ code, name: countryName(code, locale) }))
  return [...headOptions, ...options.filter((option) => !headSet.has(option.code))]
}
