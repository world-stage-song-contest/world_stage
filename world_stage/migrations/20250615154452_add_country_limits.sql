BEGIN TRANSACTION;

ALTER TABLE country ADD COLUMN available_from INTEGER DEFAULT 0;
ALTER TABLE country ADD COLUMN available_until INTEGER DEFAULT 9999;

-- make Soviet Union available until 1977
UPDATE country
SET available_until = 1977
WHERE id = 'SUN';

-- make Latvia available from 1978
UPDATE country
SET available_from = 1978
WHERE id = 'LVA';

-- make Russia, Ukraine, Belarus, Moldova, Georgia, Armenia, Azerbaijan
-- Lithuania, Estonia, Kazakhstan, Kyrgyzstan, Uzbekistan, Tajikistan, Turkmenistan
-- available from 1979
UPDATE country
SET available_from = 1979
WHERE id IN ('RUS', 'UKR', 'BLR', 'MDA', 'GEO', 'ARM', 'AZE', 'LTU', 'EST', 'KAZ', 'KGZ', 'UZB', 'TJK', 'TKM');

-- make Yugoslavia available until 1991
UPDATE country
SET available_until = 1991
WHERE id = 'YUG';

-- make Bosnia and Herzegovina, Slovenia, Croatia, North Macedonia and Serbia and Montenegro available from 1992
UPDATE country
SET available_from = 1992
WHERE id IN ('BIH', 'SVN', 'HRV', 'MKD', 'SCG');

-- make Serbia and Montenegro available until 2006
UPDATE country
SET available_until = 2006
WHERE id = 'SCG';

-- make Montenegro and Serbia available from 2007
UPDATE country
SET available_from = 2007
WHERE id IN ('MNE', 'SRB');

-- make Kosovo available from 2008
UPDATE country
SET available_from = 2008
WHERE id = 'XXK';

-- make Czechoslovakia available until 1992
UPDATE country
SET available_until = 1992
WHERE id = 'CSK';

-- make Czechia and Slovakia available from 1993
UPDATE country
SET available_from = 1993
WHERE id IN ('CZE', 'SVK');

-- make East Germany available until 1990
UPDATE country
SET available_until = 1990
WHERE id = 'DDR';

-- make Zimbabwe available from 1980
UPDATE country
SET available_from = 1980
WHERE id = 'ZWE';

-- make Eriterea available from 1993
UPDATE country
SET available_from = 1993
WHERE id = 'ERI';

-- make South Sudan available from 2011
UPDATE country
SET available_from = 2011
WHERE id = 'SSD';

-- make Timor-Leste available from 2002
UPDATE country
SET available_from = 2002
WHERE id = 'TLS';

-- make United Kingdom available until 1978
UPDATE country
SET available_until = 1978
WHERE id = 'GBR';

-- make England, Scotland and Wales available from 1979
UPDATE country
SET available_from = 1979
WHERE id IN ('GBR-ENG', 'GBR-SCT', 'GBR-WAL');

-- make Marshall Islands and Micronesia available from 1986
UPDATE country
SET available_from = 1986
WHERE id IN ('MHL', 'FSM');

-- make Namibia available from 1990
UPDATE country
SET available_from = 1990
WHERE id = 'NAM';

-- make Palau available from 1994
UPDATE country
SET available_from = 1994
WHERE id = 'PLW';

-- delete NULL songs
DELETE FROM song
WHERE title IS NULL OR title = '';

-- make all countries participating
UPDATE country
SET is_participating = 1;

-- exclude Aaland Islands, American Samoa, Anguilla, Bermuda, British Virgin Islands, Cayman Islands,
-- Christmas Island, Cocos (Keeling) Islands, Cook Islands, Curacao, Faroe Islands, Gibraltar, Guam,
-- Guernsey, Isle of Man, Jersey, Montserrat, Norfolk Island, Northern Mariana Islands,
-- Pitcairn Islands, Saint Barthelemy, Saint Helena, Antarctica, Guadeloupe, Reunion,
-- Saint Martin (French part), Saint Pierre and Miquelon, Tokelau, French Guiana,
-- Sint Maarten (Dutch part), South Georgia and the South Sandwich Islands, Turks and Caicos Islands,
-- United States Virgin Islands, North Korea, Vatican City, Wallis and Futuna Islands, Basque Country,
-- Sao Tome and Principe, Solomon Islands, Marshall Islands, European Union, Northern Ireland,
-- Rest of the World (XRW), and Unknown (XXX), Niue, Mayotte, Aruba
-- from participating countries
UPDATE country
SET is_participating = 0
WHERE id IN (
    'ALA', 'ASM', 'AIA', 'BMU', 'VGB', 'CYM', 'CXR', 'CCK', 'COK', 'CUW',
    'GIB', 'GUM', 'GGY', 'IMN', 'JEY', 'MSR', 'NFK', 'MNP',
    'PCN', 'SBM', 'SHN', 'MAF', 'SPM', 'SXM', 'ATA', 'TKL', 'EUE',
    'SGS', 'TCA', 'VIR', 'PRK', 'VAT', 'WLF', 'ESP-PV', 'STP',
    'SLB', 'MHL', 'XRW', 'XXX', 'GLP', 'REU', 'GBR-NIR', 'GUF',
    'NIU', 'MYT', 'ABW', 'BVT'
);


COMMIT;