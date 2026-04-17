# Mapping fichiers sources → tests

## 1. Word (D01-D12)

| Test | Fichier | Contenu |
|------|---------|---------|
| D01 | D01_anonymized.docx | FR, rapport audit DAF : comptabilité/paie/trésorerie. 6pg, 3 tables, 3 imgs, footer |
| D02 | D02.docx | EN, CGV (General Terms & Conditions of Sale). ~11pg, pas de tables/images |
| D03 | *(sortie de D02)* | Chaîné : prend le DOCX avec tracked changes produit par D02 |
| D04 | D04_test_03_proposition_commerciale_variantes.docx | FR, proposition commerciale formation IA. 1pg court |
| D05 | *(markdown inline)* | Pas de fichier — le markdown est dans le prompt |
| D06 | D06_a_anonymized.docx + D06_b_anonymized.docx | FR, proposition technique (3pg, 2 tables, 7 imgs) + conditions d'exécution (2pg) |
| D07 | D07.docx | EN, rapport rémunération dirigeants FY2025. ~13pg, 37 tables, 5 imgs |
| D08 | D08.docx | EN, même contenu que D07 mais plus lourd (419 KB, images différentes) |
| D09 | D09.doc | Format legacy .doc binaire (186 KB) |
| D11 | D11.docx | FR, contrat CDD template. ~6pg, pas de tables/images |
| D12 | D12.docx | EN, formulaire d'autorisation. ~9pg, 23 tables, 1 img, 8 sections |

## 2. PowerPoint (P01-P12)

| Test | Fichier | Contenu |
|------|---------|---------|
| P01 | *(création from scratch)* | Pas de fichier — pptxgenjs |
| P01b | *(création from template)* | Pas de fichier — template OBA |
| P02 | P02.pptx | EN, "Researcher Deep Dive" AI research. 20 slides, tables, notes (9.1 MB) |
| P03 | P03.pptx | Identique à P02 (même md5). Pour analyse thumbnails |
| P04 | P04.pptx | FR, 1 slide daté 30/06/2025 (679 KB) |
| P05 | P05.pptx | FR, "Conseil et Opérateur d'IA" deck corporate. 49 slides (19.1 MB) |
| P06 | *(réutiliser P02)* | Conversion PPTX → PDF |
| P07 | *(réutiliser P05)* | Extraction texte markdown |
| P08 | *(création from scratch)* | Pas de fichier — pptxgenjs avec charts |
| P09 | P09.pptx | Template vide OnBehalf : 0 slides, 47 layouts (628 KB) |
| P10 | P10.pptx | FR, deck corporate. 26 slides (5.0 MB) |
| P11 | *(création from scratch)* | Pas de fichier — pptxgenjs |
| P12 | P12.potx | Template .potx : 8 slides templates, 18 layouts (440 KB) |

## 3. Excel (X01-X12)

| Test | Fichier | Contenu |
|------|---------|---------|
| X01 | X01_reference_budget_previsionnel_complexe.xlsx | Budget : 5 onglets (Q1-Q4 + Synthèse), formules inter-onglets |
| X02 | X02_donnees_commerciales_complexes.xlsx | CRM export : 3000 commandes, 28 colonnes |
| X03 | X03_formules_recalcul_complexes.xlsx | Simulation pricing : 2 onglets, formules chaînées sur B2 |
| X04 | X04_ventes_mensuelles_input_complexe.xlsx | Ventes 24 mois par canal |
| X05 | X05_ancien_format_complexe.xls | Legacy .xls : 2 onglets annuels |
| X06 | X06_transactions_pivot_input_complexe.xlsx | 5000 transactions pour pivots |
| X07 | X07_reference_objectifs_conditionnels_complexe.xlsx | Objectifs vendeurs 10×12 + mise en forme conditionnelle |
| X08 | X08_source_export_pdf_complexe.xlsx | Board pack 17 colonnes pour export PDF paysage |
| X09 | X09_rapport_complexe_janvier.xlsx + _fevrier.xlsx + _mars.xlsx | 3 P&L mensuels à fusionner |
| X10 | X10_donnees_sales_complexes.xlsx | CRM sale : 332 lignes, 8 doublons, dates mixtes |
| X11 | X11_reference_tresorerie_12_mois_complexe.xlsx | Trésorerie 12 mois : Hypothèses + Treasury |
| X12 | X12_donnees_rh_complexes.xlsx | RH : 850 employés, 10200 paies, 1200 absences, 4 onglets |

## 4. PDF (F01-F12)

| Test | Fichier(s) | Contenu |
|------|-----------|---------|
| F01 | F01_contrat/*.pdf (3 fichiers) | Contrats EN : professional-services (2pg), service-agreement (15pg), termination (1pg) |
| F02 | F02_tableaux/*.pdf (6 fichiers) | PDFs avec tableaux : facture, relevé bancaire, rapports |
| F03 | F03_ocr/*.pdf (3 fichiers) | PDFs scannés (image-only) : courrier, document, scan |
| F04 | F04_fusion/*.pdf (3 fichiers) | 3 PDFs à fusionner : rapport + annexes + conditions |
| F05 | F05_split/document-15pages.pdf | Service agreement 15 pages |
| F06 | F06_integrite/*.pdf (3 fichiers) | Corrompu (1KB) + chiffré + normal |
| F07 | F07_images/document-a-convertir.pdf | CA WARN report 16pg pour conversion en images |
| F08 | F08_metadata/invoice-metadata.pdf | Facture 1pg pour lecture/écriture métadonnées |
| F09 | F09_rotation/document-pages-retournees.pdf | 15pg avec pages retournées 180° |
| F10 | F10_watermark/document-a-watermarker.pdf | 15pg pour ajout watermark |
| F11 | F11_compression/sample-heavy-25mb.pdf | PDF lourd 104 MB (report 50pg) |
| F12 | F12_pipeline_ocr/*.pdf (2 fichiers) | Relevé bancaire : original (texte) + scanné (image) |

## 5. Médias (M01-M12)

| Test | Fichier(s) | Contenu |
|------|-----------|---------|
| M01 | file_example_MOV_640_800kB.mov | MOV 640px (778 KB) pour conversion MP4 |
| M02 | sample-10s.mp4 | MP4 ~10s (5.3 MB) pour extraction audio |
| M03 | sample_1280x720_surfing_with_audio.avi | AVI 1280x720 surfing ~3min (26 MB) pour découpe |
| M04 | sample-10s.mp4 | MP4 ~10s pour création GIF |
| M05 | sample-30s.mp4 | MP4 ~30s (21 MB) pour analyse ffprobe |
| M06 | Pastoral_Landscape_-_Asher_Durand.jpg | JPG 2724x1760 (4.7 MB) pour redimensionnement |
| M07 | Landscape_big_river_in_mountains.jpg | JPG 1600x1066 (194 KB) pour ajout texte |
| M08 | sample-15s.mp4 + sample-20s.mp4 | 2 MP4 pour concaténation |
| M09 | sample-10s.mp4 + sample-12s.mp3 | Vidéo + audio pour mixage |
| M10 | sample-boat-400x300.png + sample-city-park-400x300.jpg + sample-clouds-400x300.jpg + sample-birch-400x300.jpg | 4 images 400x300 pour mosaïque 2×2 |
| M11 | sample_1280x720_surfing_with_audio.avi | AVI surfing pour extraction frame |
| M12 | sample-boat-400x300.png + sample-city-park-400x300.jpg + sample-clouds-400x300.jpg + sample-clouds2-400x300.png + sample-bumblebee-400x300.png | 5 images formats variés pour batch conversion |

## 6. DataViz/CSV (A01-A12)

| Test | Fichier | Contenu |
|------|---------|---------|
| A01 | A01_A02_A08_ventes_commerciales.csv | 2600 lignes × 14 cols. Ventes commerciales |
| A02 | A01_A02_A08_ventes_commerciales.csv | Même fichier que A01 pour dashboard |
| A03 | A03_satisfaction_par_canal.csv | 420 lignes × 3 cols. Satisfaction par canal |
| A04 | A04_dataset_correlation.csv | 500 lignes × 8 cols. Métriques marketing/ventes |
| A05 | A05_surface_prix_regression.csv | 200 lignes × 2 cols. Surface vs prix |
| A06 | A06_clients_clustering.csv | 1050 lignes × 6 cols. Données clients |
| A07 | A07_serie_temporelle_ca_quotidien.csv | 730 lignes × 2 cols. CA quotidien 2 ans |
| A08 | A01_A02_A08_ventes_commerciales.csv | Même fichier pour export multi-onglets |
| A09 | A09_transactions_anomalies.csv | 2230 lignes × 5 cols. Transactions avec outliers |
| A10 | A10_avant_apres_satisfaction.csv | 50 lignes × 3 cols. Avant/après satisfaction |
| A11 | A11_texte_wordcloud.txt | Texte FR business (1415 chars) |
| A12 | A12_pipeline_dirty_sales.csv | 1255 lignes × 10 cols. CSV sale (35 doublons, dates manquantes) |
