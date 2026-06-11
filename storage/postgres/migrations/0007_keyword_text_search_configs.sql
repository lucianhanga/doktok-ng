-- M5.7 fine-tune: non-stemming keyword text-search configs.
-- Each config uses the 'simple' template (lowercase, no stemming) plus the language's stopword
-- list, so extracted keyword terms are readable real words with stopwords removed.

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_danish CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_danish_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_danish_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = danish);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_danish (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_danish
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_danish_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_dutch CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_dutch_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_dutch_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = dutch);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_dutch (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_dutch
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_dutch_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_english CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_english_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_english_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = english);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_english (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_english
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_english_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_finnish CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_finnish_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_finnish_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = finnish);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_finnish (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_finnish
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_finnish_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_french CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_french_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_french_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = french);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_french (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_french
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_french_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_german CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_german_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_german_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = german);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_german (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_german
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_german_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_hungarian CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_hungarian_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_hungarian_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = hungarian);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_hungarian (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_hungarian
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_hungarian_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_italian CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_italian_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_italian_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = italian);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_italian (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_italian
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_italian_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_norwegian CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_norwegian_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_norwegian_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = norwegian);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_norwegian (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_norwegian
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_norwegian_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_portuguese CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_portuguese_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_portuguese_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = portuguese);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_portuguese (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_portuguese
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_portuguese_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_russian CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_russian_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_russian_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = russian);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_russian (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_russian
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_russian_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_spanish CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_spanish_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_spanish_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = spanish);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_spanish (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_spanish
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_spanish_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_swedish CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_swedish_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_swedish_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = swedish);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_swedish (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_swedish
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_swedish_dict;

DROP TEXT SEARCH CONFIGURATION IF EXISTS doktok_kw_turkish CASCADE;
DROP TEXT SEARCH DICTIONARY IF EXISTS doktok_kw_turkish_dict CASCADE;
CREATE TEXT SEARCH DICTIONARY doktok_kw_turkish_dict (TEMPLATE = pg_catalog.simple, STOPWORDS = turkish);
CREATE TEXT SEARCH CONFIGURATION doktok_kw_turkish (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION doktok_kw_turkish
    ALTER MAPPING FOR asciiword, word, asciihword, hword_asciipart, hword, hword_part
    WITH doktok_kw_turkish_dict;
