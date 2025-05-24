INSERT INTO country (code, name) VALUES ('GBR', 'United Kingdom');
INSERT INTO country (code, name) VALUES ('FRA', 'France');
INSERT INTO country (code, name) VALUES ('DEU', 'Germany');
INSERT INTO country (code, name) VALUES ('ITA', 'Italy');
INSERT INTO country (code, name) VALUES ('ESP', 'Spain');
INSERT INTO country (code, name) VALUES ('NLD', 'Netherlands');
INSERT INTO country (code, name) VALUES ('SWE', 'Sweden');
INSERT INTO country (code, name) VALUES ('NOR', 'Norway');
INSERT INTO country (code, name) VALUES ('DNK', 'Denmark');
INSERT INTO country (code, name) VALUES ('FIN', 'Finland');
INSERT INTO country (code, name) VALUES ('POL', 'Poland');
INSERT INTO country (code, name) VALUES ('CHE', 'Switzerland');
INSERT INTO country (code, name) VALUES ('AUT', 'Austria');
INSERT INTO country (code, name) VALUES ('BEL', 'Belgium');
INSERT INTO country (code, name) VALUES ('IRL', 'Ireland');
INSERT INTO country (code, name) VALUES ('CZE', 'Czechia');
INSERT INTO country (code, name) VALUES ('HUN', 'Hungary');
INSERT INTO country (code, name) VALUES ('SVK', 'Slovakia');
INSERT INTO country (code, name) VALUES ('ROU', 'Romania');
INSERT INTO country (code, name) VALUES ('BGR', 'Bulgaria');
INSERT INTO country (code, name) VALUES ('GRC', 'Greece');
INSERT INTO country (code, name) VALUES ('ISR', 'Israel');
INSERT INTO country (code, name) VALUES ('SRB', 'Serbia');
INSERT INTO country (code, name) VALUES ('MDA', 'Moldova');
INSERT INTO country (code, name) VALUES ('UKR', 'Ukraine');
INSERT INTO country (code, name) VALUES ('LTU', 'Lithuania');
INSERT INTO country (code, name) VALUES ('LVA', 'Latvia');
INSERT INTO country (code, name) VALUES ('EST', 'Estonia');

INSERT INTO point_system (number) VALUES (10);

INSERT INTO point (point_system_id, score) VALUES (1, 12);
INSERT INTO point (point_system_id, score) VALUES (1, 10);
INSERT INTO point (point_system_id, score) VALUES (1, 8);
INSERT INTO point (point_system_id, score) VALUES (1, 7);
INSERT INTO point (point_system_id, score) VALUES (1, 6);
INSERT INTO point (point_system_id, score) VALUES (1, 5);
INSERT INTO point (point_system_id, score) VALUES (1, 4);
INSERT INTO point (point_system_id, score) VALUES (1, 3);
INSERT INTO point (point_system_id, score) VALUES (1, 2);
INSERT INTO point (point_system_id, score) VALUES (1, 1);

INSERT INTO language (name, tag, suppress_script) VALUES ('English', 'en', 'Latn');
INSERT INTO language (name, tag, suppress_script) VALUES ('French', 'fr', 'Latn');

INSERT INTO year (id) VALUES (2023);
INSERT INTO year (id) VALUES (2024);
INSERT INTO year (id) VALUES (2025);

INSERT INTO show (year_id, point_system_id, show_name, short_name, voting_opens, voting_closes, date, dtf, sc, special, allow_access_type)
    VALUES (2023, 1, 'Semi-Final 1', 'sf1', '2023-05-01 00:00:00', '2023-05-08 23:59:59', '2023-05-08', 10, 0, 0, 'full');

INSERT INTO show (year_id, point_system_id, show_name, short_name, voting_opens, voting_closes, date, dtf, sc, special, allow_access_type)
    VALUES (2023, 1, 'Semi-Final 2', 'sf2', '2023-05-02 00:00:00', '2023-05-09 23:59:59', '2023-05-09', 10, 0, 0, 'full');

INSERT INTO show (year_id, point_system_id, show_name, short_name, voting_opens, voting_closes, date, dtf, sc, special, allow_access_type)
    VALUES (2023, 1, 'Final', 'f', '2023-05-10 00:00:00', '2023-05-17 23:59:59', '2023-08-17', 10, 0, 0, 'full');

INSERT INTO song (year_id, )