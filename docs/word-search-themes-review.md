# Word Search — Theme Bank for Review

Generated 2026-09-04. **140 themes**, 10 words each, 1400 words total.

Installed in `app/services/word_search.py` as of commit `cc9a938`, replacing the
previous 7-theme bank (which repeated weekly). The original 7 are kept as entries 1-7.

Every theme has been machine-verified: A-Z only, 10 unique words, none longer than the 12x12 grid, and all 10 words provably packable by the real `place_words` algorithm across 400 different date seeds.
What is NOT machine-checked is whether the words are *correct* and *appropriate*.
Seven bad entries were caught by eye before shipping (truncations like `TRICERATOP`,
and `HELMAND`/`HUMBLE` listed as Indian festivals); a well-formed but wrong word
passes every automated check, so this list is worth re-reading rather than trusted.

`tests/test_word_search.py` enforces the machine-checkable invariants on every run.

---

## Part 1 — Schedule

Theme for a date is `THEMES[int(YYYYMMDD) % N]`, so this mapping is fixed as long as the bank stays at 140 entries. **Adding or removing even one theme reshuffles every date below.**

First 400 days from 2026-09-05:

| Date | Day | # | Theme |
|---|---|---|---|
| 2026-09-05 | Sat | 106 | Swimming |
| 2026-09-06 | Sun | 107 | Olympics |
| 2026-09-07 | Mon | 108 | Chess |
| 2026-09-08 | Tue | 109 | Hockey |
| 2026-09-09 | Wed | 110 | Badminton |
| 2026-09-10 | Thu | 111 | Cycling |
| 2026-09-11 | Fri | 112 | Motorsport |
| 2026-09-12 | Sat | 113 | Kitchen |
| 2026-09-13 | Sun | 114 | Tools |
| 2026-09-14 | Mon | 115 | Furniture |
| 2026-09-15 | Tue | 116 | Clothing |
| 2026-09-16 | Wed | 117 | Jewellery |
| 2026-09-17 | Thu | 118 | Stationery |
| 2026-09-18 | Fri | 119 | Camping |
| 2026-09-19 | Sat | 120 | Weather at Home |
| 2026-09-20 | Sun | 121 | Colours |
| 2026-09-21 | Mon | 122 | Shapes |
| 2026-09-22 | Tue | 123 | Farming |
| 2026-09-23 | Wed | 124 | Journalism |
| 2026-09-24 | Thu | 125 | Engineering |
| 2026-09-25 | Fri | 126 | Cooking Crafts |
| 2026-09-26 | Sat | 127 | Fishing |
| 2026-09-27 | Sun | 128 | Pottery |
| 2026-09-28 | Mon | 129 | Carpentry |
| 2026-09-29 | Tue | 130 | Weaving |
| 2026-09-30 | Wed | 131 | Seasons |
| 2026-10-01 | Thu | 62 | Photography |
| 2026-10-02 | Fri | 63 | Cinema |
| 2026-10-03 | Sat | 64 | Theatre |
| 2026-10-04 | Sun | 65 | Literature |
| 2026-10-05 | Mon | 66 | Poetry |
| 2026-10-06 | Tue | 67 | Instruments |
| 2026-10-07 | Wed | 68 | Museums |
| 2026-10-08 | Thu | 69 | Mythology |
| 2026-10-09 | Fri | 70 | Folklore |
| 2026-10-10 | Sat | 71 | Fruits |
| 2026-10-11 | Sun | 72 | Vegetables |
| 2026-10-12 | Mon | 73 | Grains |
| 2026-10-13 | Tue | 74 | Herbs |
| 2026-10-14 | Wed | 75 | Baking |
| 2026-10-15 | Thu | 76 | Beverages |
| 2026-10-16 | Fri | 77 | Desserts |
| 2026-10-17 | Sat | 78 | Seafood |
| 2026-10-18 | Sun | 79 | Breakfast |
| 2026-10-19 | Mon | 80 | Flowers |
| 2026-10-20 | Tue | 81 | Trees |
| 2026-10-21 | Wed | 82 | Insects |
| 2026-10-22 | Thu | 83 | Butterflies |
| 2026-10-23 | Fri | 84 | Reptiles |
| 2026-10-24 | Sat | 85 | Mammals |
| 2026-10-25 | Sun | 86 | Fish |
| 2026-10-26 | Mon | 87 | Seashore |
| 2026-10-27 | Tue | 88 | Garden |
| 2026-10-28 | Wed | 89 | Mushrooms |
| 2026-10-29 | Thu | 90 | Aircraft |
| 2026-10-30 | Fri | 91 | Ships |
| 2026-10-31 | Sat | 92 | Trains |
| 2026-11-01 | Sun | 22 | Indian Languages |
| 2026-11-02 | Mon | 23 | Indian Textiles |
| 2026-11-03 | Tue | 24 | Indian Railways |
| 2026-11-04 | Wed | 25 | Himalayas |
| 2026-11-05 | Thu | 26 | Indian Ocean |
| 2026-11-06 | Fri | 27 | Ayurveda |
| 2026-11-07 | Sat | 28 | Yoga |
| 2026-11-08 | Sun | 29 | Cricket |
| 2026-11-09 | Mon | 30 | Indian Independence |
| 2026-11-10 | Tue | 31 | Temples |
| 2026-11-11 | Wed | 32 | Indian Markets |
| 2026-11-12 | Thu | 33 | World Rivers |
| 2026-11-13 | Fri | 34 | Mountains |
| 2026-11-14 | Sat | 35 | Deserts |
| 2026-11-15 | Sun | 36 | Islands |
| 2026-11-16 | Mon | 37 | Oceans and Seas |
| 2026-11-17 | Tue | 38 | Capitals |
| 2026-11-18 | Wed | 39 | Volcanoes |
| 2026-11-19 | Thu | 40 | Rainforest |
| 2026-11-20 | Fri | 41 | Polar Regions |
| 2026-11-21 | Sat | 42 | Caves |
| 2026-11-22 | Sun | 43 | Chemistry |
| 2026-11-23 | Mon | 44 | Physics |
| 2026-11-24 | Tue | 45 | Biology |
| 2026-11-25 | Wed | 46 | Astronomy |
| 2026-11-26 | Thu | 47 | Genetics |
| 2026-11-27 | Fri | 48 | Computers |
| 2026-11-28 | Sat | 49 | Internet |
| 2026-11-29 | Sun | 50 | Robotics |
| 2026-11-30 | Mon | 51 | Mathematics |
| 2026-12-01 | Tue | 122 | Shapes |
| 2026-12-02 | Wed | 123 | Farming |
| 2026-12-03 | Thu | 124 | Journalism |
| 2026-12-04 | Fri | 125 | Engineering |
| 2026-12-05 | Sat | 126 | Cooking Crafts |
| 2026-12-06 | Sun | 127 | Fishing |
| 2026-12-07 | Mon | 128 | Pottery |
| 2026-12-08 | Tue | 129 | Carpentry |
| 2026-12-09 | Wed | 130 | Weaving |
| 2026-12-10 | Thu | 131 | Seasons |
| 2026-12-11 | Fri | 132 | Time |
| 2026-12-12 | Sat | 133 | Light |
| 2026-12-13 | Sun | 134 | Sound |
| 2026-12-14 | Mon | 135 | Travel |
| 2026-12-15 | Tue | 136 | Games |
| 2026-12-16 | Wed | 137 | Books |
| 2026-12-17 | Thu | 138 | Weather Signs |
| 2026-12-18 | Fri | 139 | Emotions |
| 2026-12-19 | Sat | 140 | Money |
| 2026-12-20 | Sun | 1 | Space |
| 2026-12-21 | Mon | 2 | India |
| 2026-12-22 | Tue | 3 | Nature |
| 2026-12-23 | Wed | 4 | Science |
| 2026-12-24 | Thu | 5 | Newspaper |
| 2026-12-25 | Fri | 6 | Wildlife |
| 2026-12-26 | Sat | 7 | Geography |
| 2026-12-27 | Sun | 8 | Indian Rivers |
| 2026-12-28 | Mon | 9 | Indian Cities |
| 2026-12-29 | Tue | 10 | Indian States |
| 2026-12-30 | Wed | 11 | Indian Festivals |
| 2026-12-31 | Thu | 12 | Indian Cuisine |
| 2027-01-01 | Fri | 62 | Photography |
| 2027-01-02 | Sat | 63 | Cinema |
| 2027-01-03 | Sun | 64 | Theatre |
| 2027-01-04 | Mon | 65 | Literature |
| 2027-01-05 | Tue | 66 | Poetry |
| 2027-01-06 | Wed | 67 | Instruments |
| 2027-01-07 | Thu | 68 | Museums |
| 2027-01-08 | Fri | 69 | Mythology |
| 2027-01-09 | Sat | 70 | Folklore |
| 2027-01-10 | Sun | 71 | Fruits |
| 2027-01-11 | Mon | 72 | Vegetables |
| 2027-01-12 | Tue | 73 | Grains |
| 2027-01-13 | Wed | 74 | Herbs |
| 2027-01-14 | Thu | 75 | Baking |
| 2027-01-15 | Fri | 76 | Beverages |
| 2027-01-16 | Sat | 77 | Desserts |
| 2027-01-17 | Sun | 78 | Seafood |
| 2027-01-18 | Mon | 79 | Breakfast |
| 2027-01-19 | Tue | 80 | Flowers |
| 2027-01-20 | Wed | 81 | Trees |
| 2027-01-21 | Thu | 82 | Insects |
| 2027-01-22 | Fri | 83 | Butterflies |
| 2027-01-23 | Sat | 84 | Reptiles |
| 2027-01-24 | Sun | 85 | Mammals |
| 2027-01-25 | Mon | 86 | Fish |
| 2027-01-26 | Tue | 87 | Seashore |
| 2027-01-27 | Wed | 88 | Garden |
| 2027-01-28 | Thu | 89 | Mushrooms |
| 2027-01-29 | Fri | 90 | Aircraft |
| 2027-01-30 | Sat | 91 | Ships |
| 2027-01-31 | Sun | 92 | Trains |
| 2027-02-01 | Mon | 22 | Indian Languages |
| 2027-02-02 | Tue | 23 | Indian Textiles |
| 2027-02-03 | Wed | 24 | Indian Railways |
| 2027-02-04 | Thu | 25 | Himalayas |
| 2027-02-05 | Fri | 26 | Indian Ocean |
| 2027-02-06 | Sat | 27 | Ayurveda |
| 2027-02-07 | Sun | 28 | Yoga |
| 2027-02-08 | Mon | 29 | Cricket |
| 2027-02-09 | Tue | 30 | Indian Independence |
| 2027-02-10 | Wed | 31 | Temples |
| 2027-02-11 | Thu | 32 | Indian Markets |
| 2027-02-12 | Fri | 33 | World Rivers |
| 2027-02-13 | Sat | 34 | Mountains |
| 2027-02-14 | Sun | 35 | Deserts |
| 2027-02-15 | Mon | 36 | Islands |
| 2027-02-16 | Tue | 37 | Oceans and Seas |
| 2027-02-17 | Wed | 38 | Capitals |
| 2027-02-18 | Thu | 39 | Volcanoes |
| 2027-02-19 | Fri | 40 | Rainforest |
| 2027-02-20 | Sat | 41 | Polar Regions |
| 2027-02-21 | Sun | 42 | Caves |
| 2027-02-22 | Mon | 43 | Chemistry |
| 2027-02-23 | Tue | 44 | Physics |
| 2027-02-24 | Wed | 45 | Biology |
| 2027-02-25 | Thu | 46 | Astronomy |
| 2027-02-26 | Fri | 47 | Genetics |
| 2027-02-27 | Sat | 48 | Computers |
| 2027-02-28 | Sun | 49 | Internet |
| 2027-03-01 | Mon | 122 | Shapes |
| 2027-03-02 | Tue | 123 | Farming |
| 2027-03-03 | Wed | 124 | Journalism |
| 2027-03-04 | Thu | 125 | Engineering |
| 2027-03-05 | Fri | 126 | Cooking Crafts |
| 2027-03-06 | Sat | 127 | Fishing |
| 2027-03-07 | Sun | 128 | Pottery |
| 2027-03-08 | Mon | 129 | Carpentry |
| 2027-03-09 | Tue | 130 | Weaving |
| 2027-03-10 | Wed | 131 | Seasons |
| 2027-03-11 | Thu | 132 | Time |
| 2027-03-12 | Fri | 133 | Light |
| 2027-03-13 | Sat | 134 | Sound |
| 2027-03-14 | Sun | 135 | Travel |
| 2027-03-15 | Mon | 136 | Games |
| 2027-03-16 | Tue | 137 | Books |
| 2027-03-17 | Wed | 138 | Weather Signs |
| 2027-03-18 | Thu | 139 | Emotions |
| 2027-03-19 | Fri | 140 | Money |
| 2027-03-20 | Sat | 1 | Space |
| 2027-03-21 | Sun | 2 | India |
| 2027-03-22 | Mon | 3 | Nature |
| 2027-03-23 | Tue | 4 | Science |
| 2027-03-24 | Wed | 5 | Newspaper |
| 2027-03-25 | Thu | 6 | Wildlife |
| 2027-03-26 | Fri | 7 | Geography |
| 2027-03-27 | Sat | 8 | Indian Rivers |
| 2027-03-28 | Sun | 9 | Indian Cities |
| 2027-03-29 | Mon | 10 | Indian States |
| 2027-03-30 | Tue | 11 | Indian Festivals |
| 2027-03-31 | Wed | 12 | Indian Cuisine |
| 2027-04-01 | Thu | 82 | Insects |
| 2027-04-02 | Fri | 83 | Butterflies |
| 2027-04-03 | Sat | 84 | Reptiles |
| 2027-04-04 | Sun | 85 | Mammals |
| 2027-04-05 | Mon | 86 | Fish |
| 2027-04-06 | Tue | 87 | Seashore |
| 2027-04-07 | Wed | 88 | Garden |
| 2027-04-08 | Thu | 89 | Mushrooms |
| 2027-04-09 | Fri | 90 | Aircraft |
| 2027-04-10 | Sat | 91 | Ships |
| 2027-04-11 | Sun | 92 | Trains |
| 2027-04-12 | Mon | 93 | Cars |
| 2027-04-13 | Tue | 94 | Bicycles |
| 2027-04-14 | Wed | 95 | Bridges |
| 2027-04-15 | Thu | 96 | Airport |
| 2027-04-16 | Fri | 97 | Hotel |
| 2027-04-17 | Sat | 98 | Library |
| 2027-04-18 | Sun | 99 | Hospital |
| 2027-04-19 | Mon | 100 | School |
| 2027-04-20 | Tue | 101 | Office |
| 2027-04-21 | Wed | 102 | Bank |
| 2027-04-22 | Thu | 103 | Football |
| 2027-04-23 | Fri | 104 | Tennis |
| 2027-04-24 | Sat | 105 | Athletics |
| 2027-04-25 | Sun | 106 | Swimming |
| 2027-04-26 | Mon | 107 | Olympics |
| 2027-04-27 | Tue | 108 | Chess |
| 2027-04-28 | Wed | 109 | Hockey |
| 2027-04-29 | Thu | 110 | Badminton |
| 2027-04-30 | Fri | 111 | Cycling |
| 2027-05-01 | Sat | 42 | Caves |
| 2027-05-02 | Sun | 43 | Chemistry |
| 2027-05-03 | Mon | 44 | Physics |
| 2027-05-04 | Tue | 45 | Biology |
| 2027-05-05 | Wed | 46 | Astronomy |
| 2027-05-06 | Thu | 47 | Genetics |
| 2027-05-07 | Fri | 48 | Computers |
| 2027-05-08 | Sat | 49 | Internet |
| 2027-05-09 | Sun | 50 | Robotics |
| 2027-05-10 | Mon | 51 | Mathematics |
| 2027-05-11 | Tue | 52 | Medicine |
| 2027-05-12 | Wed | 53 | Anatomy |
| 2027-05-13 | Thu | 54 | Minerals |
| 2027-05-14 | Fri | 55 | Fossils |
| 2027-05-15 | Sat | 56 | Dinosaurs |
| 2027-05-16 | Sun | 57 | Weather |
| 2027-05-17 | Mon | 58 | Energy |
| 2027-05-18 | Tue | 59 | Painting |
| 2027-05-19 | Wed | 60 | Sculpture |
| 2027-05-20 | Thu | 61 | Architecture |
| 2027-05-21 | Fri | 62 | Photography |
| 2027-05-22 | Sat | 63 | Cinema |
| 2027-05-23 | Sun | 64 | Theatre |
| 2027-05-24 | Mon | 65 | Literature |
| 2027-05-25 | Tue | 66 | Poetry |
| 2027-05-26 | Wed | 67 | Instruments |
| 2027-05-27 | Thu | 68 | Museums |
| 2027-05-28 | Fri | 69 | Mythology |
| 2027-05-29 | Sat | 70 | Folklore |
| 2027-05-30 | Sun | 71 | Fruits |
| 2027-05-31 | Mon | 72 | Vegetables |
| 2027-06-01 | Tue | 2 | India |
| 2027-06-02 | Wed | 3 | Nature |
| 2027-06-03 | Thu | 4 | Science |
| 2027-06-04 | Fri | 5 | Newspaper |
| 2027-06-05 | Sat | 6 | Wildlife |
| 2027-06-06 | Sun | 7 | Geography |
| 2027-06-07 | Mon | 8 | Indian Rivers |
| 2027-06-08 | Tue | 9 | Indian Cities |
| 2027-06-09 | Wed | 10 | Indian States |
| 2027-06-10 | Thu | 11 | Indian Festivals |
| 2027-06-11 | Fri | 12 | Indian Cuisine |
| 2027-06-12 | Sat | 13 | Indian Spices |
| 2027-06-13 | Sun | 14 | Indian Sweets |
| 2027-06-14 | Mon | 15 | Street Food |
| 2027-06-15 | Tue | 16 | Indian Monuments |
| 2027-06-16 | Wed | 17 | Indian Music |
| 2027-06-17 | Thu | 18 | Indian Dance |
| 2027-06-18 | Fri | 19 | Indian Wildlife |
| 2027-06-19 | Sat | 20 | Indian Birds |
| 2027-06-20 | Sun | 21 | Indian Trees |
| 2027-06-21 | Mon | 22 | Indian Languages |
| 2027-06-22 | Tue | 23 | Indian Textiles |
| 2027-06-23 | Wed | 24 | Indian Railways |
| 2027-06-24 | Thu | 25 | Himalayas |
| 2027-06-25 | Fri | 26 | Indian Ocean |
| 2027-06-26 | Sat | 27 | Ayurveda |
| 2027-06-27 | Sun | 28 | Yoga |
| 2027-06-28 | Mon | 29 | Cricket |
| 2027-06-29 | Tue | 30 | Indian Independence |
| 2027-06-30 | Wed | 31 | Temples |
| 2027-07-01 | Thu | 102 | Bank |
| 2027-07-02 | Fri | 103 | Football |
| 2027-07-03 | Sat | 104 | Tennis |
| 2027-07-04 | Sun | 105 | Athletics |
| 2027-07-05 | Mon | 106 | Swimming |
| 2027-07-06 | Tue | 107 | Olympics |
| 2027-07-07 | Wed | 108 | Chess |
| 2027-07-08 | Thu | 109 | Hockey |
| 2027-07-09 | Fri | 110 | Badminton |
| 2027-07-10 | Sat | 111 | Cycling |
| 2027-07-11 | Sun | 112 | Motorsport |
| 2027-07-12 | Mon | 113 | Kitchen |
| 2027-07-13 | Tue | 114 | Tools |
| 2027-07-14 | Wed | 115 | Furniture |
| 2027-07-15 | Thu | 116 | Clothing |
| 2027-07-16 | Fri | 117 | Jewellery |
| 2027-07-17 | Sat | 118 | Stationery |
| 2027-07-18 | Sun | 119 | Camping |
| 2027-07-19 | Mon | 120 | Weather at Home |
| 2027-07-20 | Tue | 121 | Colours |
| 2027-07-21 | Wed | 122 | Shapes |
| 2027-07-22 | Thu | 123 | Farming |
| 2027-07-23 | Fri | 124 | Journalism |
| 2027-07-24 | Sat | 125 | Engineering |
| 2027-07-25 | Sun | 126 | Cooking Crafts |
| 2027-07-26 | Mon | 127 | Fishing |
| 2027-07-27 | Tue | 128 | Pottery |
| 2027-07-28 | Wed | 129 | Carpentry |
| 2027-07-29 | Thu | 130 | Weaving |
| 2027-07-30 | Fri | 131 | Seasons |
| 2027-07-31 | Sat | 132 | Time |
| 2027-08-01 | Sun | 62 | Photography |
| 2027-08-02 | Mon | 63 | Cinema |
| 2027-08-03 | Tue | 64 | Theatre |
| 2027-08-04 | Wed | 65 | Literature |
| 2027-08-05 | Thu | 66 | Poetry |
| 2027-08-06 | Fri | 67 | Instruments |
| 2027-08-07 | Sat | 68 | Museums |
| 2027-08-08 | Sun | 69 | Mythology |
| 2027-08-09 | Mon | 70 | Folklore |
| 2027-08-10 | Tue | 71 | Fruits |
| 2027-08-11 | Wed | 72 | Vegetables |
| 2027-08-12 | Thu | 73 | Grains |
| 2027-08-13 | Fri | 74 | Herbs |
| 2027-08-14 | Sat | 75 | Baking |
| 2027-08-15 | Sun | 76 | Beverages |
| 2027-08-16 | Mon | 77 | Desserts |
| 2027-08-17 | Tue | 78 | Seafood |
| 2027-08-18 | Wed | 79 | Breakfast |
| 2027-08-19 | Thu | 80 | Flowers |
| 2027-08-20 | Fri | 81 | Trees |
| 2027-08-21 | Sat | 82 | Insects |
| 2027-08-22 | Sun | 83 | Butterflies |
| 2027-08-23 | Mon | 84 | Reptiles |
| 2027-08-24 | Tue | 85 | Mammals |
| 2027-08-25 | Wed | 86 | Fish |
| 2027-08-26 | Thu | 87 | Seashore |
| 2027-08-27 | Fri | 88 | Garden |
| 2027-08-28 | Sat | 89 | Mushrooms |
| 2027-08-29 | Sun | 90 | Aircraft |
| 2027-08-30 | Mon | 91 | Ships |
| 2027-08-31 | Tue | 92 | Trains |
| 2027-09-01 | Wed | 22 | Indian Languages |
| 2027-09-02 | Thu | 23 | Indian Textiles |
| 2027-09-03 | Fri | 24 | Indian Railways |
| 2027-09-04 | Sat | 25 | Himalayas |
| 2027-09-05 | Sun | 26 | Indian Ocean |
| 2027-09-06 | Mon | 27 | Ayurveda |
| 2027-09-07 | Tue | 28 | Yoga |
| 2027-09-08 | Wed | 29 | Cricket |
| 2027-09-09 | Thu | 30 | Indian Independence |
| 2027-09-10 | Fri | 31 | Temples |
| 2027-09-11 | Sat | 32 | Indian Markets |
| 2027-09-12 | Sun | 33 | World Rivers |
| 2027-09-13 | Mon | 34 | Mountains |
| 2027-09-14 | Tue | 35 | Deserts |
| 2027-09-15 | Wed | 36 | Islands |
| 2027-09-16 | Thu | 37 | Oceans and Seas |
| 2027-09-17 | Fri | 38 | Capitals |
| 2027-09-18 | Sat | 39 | Volcanoes |
| 2027-09-19 | Sun | 40 | Rainforest |
| 2027-09-20 | Mon | 41 | Polar Regions |
| 2027-09-21 | Tue | 42 | Caves |
| 2027-09-22 | Wed | 43 | Chemistry |
| 2027-09-23 | Thu | 44 | Physics |
| 2027-09-24 | Fri | 45 | Biology |
| 2027-09-25 | Sat | 46 | Astronomy |
| 2027-09-26 | Sun | 47 | Genetics |
| 2027-09-27 | Mon | 48 | Computers |
| 2027-09-28 | Tue | 49 | Internet |
| 2027-09-29 | Wed | 50 | Robotics |
| 2027-09-30 | Thu | 51 | Mathematics |
| 2027-10-01 | Fri | 122 | Shapes |
| 2027-10-02 | Sat | 123 | Farming |
| 2027-10-03 | Sun | 124 | Journalism |
| 2027-10-04 | Mon | 125 | Engineering |
| 2027-10-05 | Tue | 126 | Cooking Crafts |
| 2027-10-06 | Wed | 127 | Fishing |
| 2027-10-07 | Thu | 128 | Pottery |
| 2027-10-08 | Fri | 129 | Carpentry |
| 2027-10-09 | Sat | 130 | Weaving |

### Repeat behaviour

- First repeat: **Shapes** on 2026-12-01, 71 days after its previous use.
- Over 2000 days: shortest gap between repeats is **69 days**, average 135 days.
- Distinct themes used in the first 2000 days: 140 of 140.

Note the mapping is `date % N`, not sequential, so month-end and year boundaries jump. Gaps are uneven by design.

---

## Part 2 — The themes

**1. Space**  
GALAXY, PLANET, COMET, ORBIT, NEBULA, ROCKET, SATURN, LUNAR, METEOR, COSMOS

**2. India**  
GANGES, LOTUS, MONSOON, HIMALAYA, SAFFRON, DELHI, MUMBAI, BENGAL, DECCAN, DIWALI

**3. Nature**  
FOREST, RIVER, OCEAN, CANYON, GLACIER, VOLCANO, MEADOW, THUNDER, BREEZE, SUNSET

**4. Science**  
ATOM, CARBON, ENERGY, NEURON, PHOTON, PLASMA, MAGNET, GRAVITY, OXYGEN, QUARTZ

**5. Newspaper**  
EDITOR, COLUMN, BYLINE, PRESS, DAILY, REPORT, PUZZLE, HEADLINE, JOURNAL, ARTICLE

**6. Wildlife**  
FALCON, PANDA, TIGER, DOLPHIN, COBRA, PEACOCK, TURTLE, LEOPARD, RABBIT, WHALE

**7. Geography**  
ISLAND, DESERT, VALLEY, PLATEAU, DELTA, LAGOON, TUNDRA, SAVANNA, ARCTIC, EQUATOR

**8. Indian Rivers**  
YAMUNA, KRISHNA, GODAVARI, NARMADA, KAVERI, BEAS, CHENAB, TAPTI, MAHANADI, SUTLEJ

**9. Indian Cities**  
CHENNAI, KOLKATA, JAIPUR, LUCKNOW, INDORE, KOCHI, PATNA, SURAT, NAGPUR, BHOPAL

**10. Indian States**  
KERALA, PUNJAB, ODISHA, GUJARAT, ASSAM, MANIPUR, TRIPURA, SIKKIM, GOA, HARYANA

**11. Indian Festivals**  
HOLI, PONGAL, ONAM, BAISAKHI, NAVRATRI, HORNBILL, LOHRI, BIHU, DUSSEHRA, UGADI

**12. Indian Cuisine**  
BIRYANI, SAMOSA, DOSA, PANEER, CHUTNEY, KORMA, PULAO, RAITA, TIKKA, HALWA

**13. Indian Spices**  
TURMERIC, CARDAMOM, CUMIN, CLOVE, PEPPER, FENNEL, GINGER, MUSTARD, NUTMEG, CINNAMON

**14. Indian Sweets**  
LADDU, JALEBI, BARFI, RASGULLA, PEDA, KHEER, MODAK, HALWA, MYSOREPAK, SANDESH

**15. Street Food**  
CHAAT, PANIPURI, VADAPAV, BHELPURI, KATHIROLL, MOMO, PAKORA, IDLI, UTTAPAM, FALOODA

**16. Indian Monuments**  
TAJMAHAL, QUTUBMINAR, REDFORT, HAWAMAHAL, AJANTA, ELLORA, KHAJURAHO, AMBER, GOLCONDA, SANCHI

**17. Indian Music**  
SITAR, TABLA, VEENA, SARANGI, SHEHNAI, TANPURA, MRIDANGAM, SANTOOR, FLUTE, RAGA

**18. Indian Dance**  
SATTRIYA, KATHAK, ODISSI, KUCHIPUDI, MOHINIYATTAM, MANIPURI, BHANGRA, GARBA, LAVANI, KATHAKALI

**19. Indian Wildlife**  
GHARIAL, NILGAI, SAMBAR, LANGUR, MACAQUE, PANGOLIN, HORNBILL, BARASINGHA, CHINKARA, DHOLE

**20. Indian Birds**  
KOEL, MYNA, BULBUL, PARAKEET, KINGFISHER, DRONGO, SUNBIRD, EGRET, LAPWING, BABBLER

**21. Indian Trees**  
BANYAN, PEEPAL, NEEM, TEAK, SANDAL, MANGO, TAMARIND, GULMOHAR, ASHOKA, MAHUA

**22. Indian Languages**  
HINDI, TAMIL, TELUGU, MARATHI, KANNADA, BENGALI, ODIA, PUNJABI, URDU, MALAYALAM

**23. Indian Textiles**  
KHADI, SILK, COTTON, BROCADE, MUSLIN, IKAT, CHIKAN, BANDHANI, PASHMINA, JAMDANI

**24. Indian Railways**  
PLATFORM, SIGNAL, COACH, ENGINE, JUNCTION, SLEEPER, PANTRY, TICKET, SIDING, EXPRESS

**25. Himalayas**  
EVEREST, LHOTSE, NANDADEVI, ANNAPURNA, SHERPA, GLACIER, AVALANCHE, SUMMIT, RIDGE, CREVASSE

**26. Indian Ocean**  
MONSOON, LAKSHADWEEP, ANDAMAN, NICOBAR, CORAL, MANGROVE, CYCLONE, CURRENT, TRENCH, ATOLL

**27. Ayurveda**  
TULSI, ASHWAGANDHA, BRAHMI, AMLA, HALDI, SHATAVARI, TRIPHALA, GILOY, DOSHA, HERBAL

**28. Yoga**  
ASANA, PRANAYAMA, MUDRA, CHAKRA, MANTRA, SHAVASANA, VINYASA, BALANCE, BREATH, POSTURE

**29. Cricket**  
WICKET, BOWLER, INNINGS, BOUNDARY, STUMPS, CREASE, SPINNER, FIELDER, CENTURY, UMPIRE

**30. Indian Independence**  
SWARAJ, CHARKHA, DANDI, TRICOLOUR, ASHOKA, REPUBLIC, FREEDOM, MARCH, PLEDGE, UNITY

**31. Temples**  
GOPURAM, SHIKHARA, MANDAPA, GARBHA, PILLAR, CARVING, GRANITE, SHRINE, BELL, LAMP

**32. Indian Markets**  
BAZAAR, HAGGLE, STALL, VENDOR, BASKET, SPICE, FABRIC, BANGLE, POTTERY, GARLAND

**33. World Rivers**  
AMAZON, DANUBE, VOLGA, MEKONG, YANGTZE, CONGO, RHINE, THAMES, ZAMBEZI, COLORADO

**34. Mountains**  
ALPS, ANDES, ROCKIES, URALS, ATLAS, ZAGROS, PYRENEES, CASCADE, SIERRA, CAUCASUS

**35. Deserts**  
SAHARA, GOBI, KALAHARI, MOJAVE, ATACAMA, SONORAN, NAMIB, PATAGONIA, ARABIAN, TAKLAMAKAN

**36. Islands**  
MADAGASCAR, SUMATRA, BORNEO, CRETE, SICILY, CUBA, TASMANIA, ICELAND, FIJI, MALDIVES

**37. Oceans and Seas**  
PACIFIC, ATLANTIC, BALTIC, CASPIAN, ADRIATIC, AEGEAN, CORAL, BERING, WEDDELL, SARGASSO

**38. Capitals**  
OTTAWA, LISBON, VIENNA, HELSINKI, NAIROBI, HAVANA, MANILA, ANKARA, OSLO, PRAGUE

**39. Volcanoes**  
VESUVIUS, ETNA, KRAKATOA, FUJI, COTOPAXI, MAUNALOA, STROMBOLI, PINATUBO, MAGMA, CRATER

**40. Rainforest**  
CANOPY, LIANA, ORCHID, TOUCAN, JAGUAR, TAPIR, HUMID, FERN, SLOTH, UNDERSTORY

**41. Polar Regions**  
ICEBERG, PENGUIN, WALRUS, TUNDRA, AURORA, PERMAFROST, FLOE, BLIZZARD, SLEDGE, NARWHAL

**42. Caves**  
STALAGMITE, CHAMBER, LIMESTONE, CAVERN, TUNNEL, SINKHOLE, GROTTO, ECHO, MINERAL, DARKNESS

**43. Chemistry**  
MOLECULE, ISOTOPE, CATALYST, SOLVENT, ACID, ALKALI, CRYSTAL, POLYMER, REAGENT, VALENCE

**44. Physics**  
VELOCITY, INERTIA, FRICTION, MOMENTUM, VOLTAGE, CURRENT, PRISM, LENS, WAVE, TORQUE

**45. Biology**  
CELL, TISSUE, ENZYME, PROTEIN, MITOSIS, SPECIES, HABITAT, ORGANISM, NUCLEUS, MEMBRANE

**46. Astronomy**  
QUASAR, PULSAR, ECLIPSE, ASTEROID, CRATER, TELESCOPE, SUPERNOVA, GALAXY, AURORA, ZENITH

**47. Genetics**  
GENOME, CHROMOSOME, MUTATION, HELIX, ALLELE, TRAIT, HEREDITY, CLONE, SEQUENCE, MARKER

**48. Computers**  
KEYBOARD, MONITOR, PROCESSOR, MEMORY, STORAGE, NETWORK, SOFTWARE, PIXEL, CURSOR, BINARY

**49. Internet**  
BROWSER, SERVER, ROUTER, DOMAIN, PACKET, UPLOAD, STREAM, COOKIE, SEARCH, BANDWIDTH

**50. Robotics**  
SENSOR, ACTUATOR, CIRCUIT, GRIPPER, SERVO, CHASSIS, FEEDBACK, AUTONOMY, MOTOR, PROGRAM

**51. Mathematics**  
ALGEBRA, GEOMETRY, FRACTION, INTEGER, MATRIX, VECTOR, THEOREM, TANGENT, PRIME, RATIO

**52. Medicine**  
VACCINE, SURGEON, DIAGNOSIS, ANTIBODY, CLINIC, REMEDY, DOSAGE, SUTURE, PULSE, THERAPY

**53. Anatomy**  
SKELETON, TENDON, ARTERY, CORNEA, LIVER, SPINE, MUSCLE, KIDNEY, MARROW, CARTILAGE

**54. Minerals**  
GRANITE, BASALT, GYPSUM, FELDSPAR, MICA, OBSIDIAN, MARBLE, SLATE, PYRITE, TOPAZ

**55. Fossils**  
AMBER, IMPRINT, SEDIMENT, TRILOBITE, AMMONITE, RELIC, EXCAVATE, STRATA, PETRIFY, SPECIMEN

**56. Dinosaurs**  
RAPTOR, STEGOSAURUS, TRICERATOPS, PTEROSAUR, FOSSIL, JURASSIC, CRETACEOUS, HERBIVORE, PREDATOR, EXTINCT

**57. Weather**  
CYCLONE, DRIZZLE, HUMIDITY, FORECAST, PRESSURE, HAILSTONE, OVERCAST, SQUALL, MONSOON, FROST

**58. Energy**  
SOLAR, TURBINE, REACTOR, BATTERY, BIOMASS, THERMAL, GRID, VOLTAGE, FUSION, DYNAMO

**59. Painting**  
CANVAS, PALETTE, PIGMENT, BRUSH, EASEL, PORTRAIT, MURAL, FRESCO, SHADING, VARNISH

**60. Sculpture**  
CHISEL, MARBLE, BRONZE, RELIEF, CASTING, MODEL, STONE, CARVE, PLINTH, TORSO

**61. Architecture**  
ARCH, DOME, COLUMN, FACADE, VAULT, ATRIUM, BALCONY, TERRACE, SPIRE, CORNICE

**62. Photography**  
SHUTTER, APERTURE, LENS, TRIPOD, EXPOSURE, FOCUS, PORTRAIT, NEGATIVE, STUDIO, FILTER

**63. Cinema**  
DIRECTOR, SCRIPT, CAMERA, EDITING, SCENE, TRAILER, PREMIERE, COSTUME, SOUNDTRACK, STUDIO

**64. Theatre**  
STAGE, CURTAIN, REHEARSE, MONOLOGUE, BACKSTAGE, PROPS, LIGHTING, AUDIENCE, SCRIPT, APPLAUSE

**65. Literature**  
NOVEL, CHAPTER, NARRATOR, PLOT, PROSE, SATIRE, MEMOIR, FABLE, EPILOGUE, IMAGERY

**66. Poetry**  
SONNET, STANZA, RHYME, METER, COUPLET, VERSE, BALLAD, HAIKU, ELEGY, REFRAIN

**67. Instruments**  
VIOLIN, TRUMPET, PIANO, CELLO, HARP, OBOE, BANJO, DRUM, CLARINET, ACCORDION

**68. Museums**  
GALLERY, EXHIBIT, CURATOR, ARTEFACT, ARCHIVE, DISPLAY, CATALOGUE, RESTORE, COLLECTION, PLAQUE

**69. Mythology**  
ORACLE, TITAN, PHOENIX, CENTAUR, OLYMPUS, TRIDENT, CHARIOT, LEGEND, MORTAL, PROPHECY

**70. Folklore**  
TALE, RIDDLE, PROVERB, TRICKSTER, CHARM, LANTERN, WANDER, VILLAGE, STORYTELLER, CUSTOM

**71. Fruits**  
MANGO, PAPAYA, GUAVA, LYCHEE, APRICOT, CHERRY, BANANA, MELON, PLUM, POMEGRANATE

**72. Vegetables**  
SPINACH, CARROT, PUMPKIN, CABBAGE, BRINJAL, RADISH, TURNIP, OKRA, BEETROOT, LETTUCE

**73. Grains**  
WHEAT, BARLEY, MILLET, QUINOA, SORGHUM, OATS, RYE, MAIZE, BASMATI, BUCKWHEAT

**74. Herbs**  
BASIL, THYME, OREGANO, PARSLEY, ROSEMARY, MINT, SAGE, CHIVE, DILL, CORIANDER

**75. Baking**  
FLOUR, YEAST, KNEAD, OVEN, PASTRY, BATTER, GLAZE, CRUST, WHISK, SPONGE

**76. Beverages**  
COFFEE, MASALA, LASSI, SHERBET, NECTAR, INFUSION, BREW, CIDER, SMOOTHIE, COCOA

**77. Desserts**  
PUDDING, SORBET, MOUSSE, CUSTARD, TRIFLE, BROWNIE, PRALINE, TOFFEE, GELATO, MERINGUE

**78. Seafood**  
PRAWN, LOBSTER, MACKEREL, SARDINE, OYSTER, SQUID, CRAB, MUSSEL, POMFRET, SALMON

**79. Breakfast**  
PORRIDGE, OMELETTE, PANCAKE, TOAST, CEREAL, YOGURT, HONEY, JUICE, MUFFIN, GRANOLA

**80. Flowers**  
JASMINE, MARIGOLD, ORCHID, TULIP, DAHLIA, HIBISCUS, LAVENDER, PRIMROSE, DAISY, CAMELLIA

**81. Trees**  
MAPLE, CEDAR, WILLOW, BIRCH, POPLAR, SPRUCE, CYPRESS, WALNUT, CHESTNUT, JUNIPER

**82. Insects**  
BEETLE, CRICKET, DRAGONFLY, MANTIS, APHID, TERMITE, HORNET, WEEVIL, FIREFLY, LOCUST

**83. Butterflies**  
MONARCH, SWALLOW, ADMIRAL, PAINTED, CHRYSALIS, NECTAR, ANTENNA, MIGRATE, PUPA, MEADOW

**84. Reptiles**  
IGUANA, GECKO, PYTHON, VIPER, TORTOISE, MONITOR, SKINK, CHAMELEON, ALLIGATOR, CROCODILE

**85. Mammals**  
OTTER, BADGER, BISON, CAMEL, GIRAFFE, LEMUR, MOOSE, PORCUPINE, WOMBAT, MEERKAT

**86. Fish**  
TROUT, CARP, TUNA, ANCHOVY, HERRING, CATFISH, GROUPER, MARLIN, PIRANHA, STINGRAY

**87. Seashore**  
PEBBLE, DRIFTWOOD, SEAWEED, BARNACLE, TIDEPOOL, DUNE, SHELL, BREAKER, STARFISH, LIGHTHOUSE

**88. Garden**  
TROWEL, COMPOST, SEEDLING, PRUNE, HEDGE, TRELLIS, MULCH, SPROUT, GREENHOUSE, WATERING

**89. Mushrooms**  
MOREL, TRUFFLE, SHIITAKE, OYSTER, BUTTON, SPORE, MYCELIUM, FUNGUS, CANOPY, DECAY

**90. Aircraft**  
FUSELAGE, PROPELLER, COCKPIT, RUDDER, HANGAR, GLIDER, ALTITUDE, RUNWAY, TURBINE, AILERON

**91. Ships**  
ANCHOR, MAST, HARBOUR, RUDDER, GALLEY, SCHOONER, FERRY, CARGO, PORTHOLE, STARBOARD

**92. Trains**  
LOCOMOTIVE, CARRIAGE, TRACK, TUNNEL, STATION, SIGNAL, FREIGHT, WHISTLE, SHUNT, TIMETABLE

**93. Cars**  
ENGINE, CLUTCH, GEARBOX, CHASSIS, BUMPER, IGNITION, RADIATOR, EXHAUST, STEERING, BRAKE

**94. Bicycles**  
PEDAL, SPOKE, HANDLEBAR, SADDLE, CHAIN, GEAR, HELMET, TYRE, FRAME, BRAKE

**95. Bridges**  
SUSPENSION, GIRDER, CABLE, PYLON, ARCH, SPAN, TRUSS, VIADUCT, PIER, DECK

**96. Airport**  
TERMINAL, BOARDING, LUGGAGE, CUSTOMS, GATE, DEPARTURE, ARRIVAL, CHECKIN, TARMAC, TRANSIT

**97. Hotel**  
LOBBY, SUITE, CONCIERGE, RESERVE, BALCONY, LAUNDRY, PORTER, BUFFET, CHECKOUT, CORRIDOR

**98. Library**  
SHELF, CATALOGUE, BORROW, VOLUME, REFERENCE, SILENCE, PERIODICAL, ARCHIVE, BINDING, READING

**99. Hospital**  
WARD, SURGERY, NURSE, TRIAGE, GURNEY, PHARMACY, MONITOR, SCALPEL, RECOVERY, STERILE

**100. School**  
BLACKBOARD, SATCHEL, LESSON, RECESS, UNIFORM, ASSEMBLY, HOMEWORK, TEACHER, CHALK, REGISTER

**101. Office**  
DESK, STAPLER, MEETING, AGENDA, FOLDER, PRINTER, MEMO, CUBICLE, ROSTER, DEADLINE

**102. Bank**  
LEDGER, DEPOSIT, INTEREST, VAULT, CHEQUE, BALANCE, CASHIER, ACCOUNT, TRANSFER, LOAN

**103. Football**  
STRIKER, GOALIE, MIDFIELD, PENALTY, OFFSIDE, CORNER, DRIBBLE, TACKLE, WHISTLE, STADIUM

**104. Tennis**  
RACQUET, VOLLEY, BASELINE, DEUCE, SERVE, RALLY, TIEBREAK, COURT, SMASH, BACKHAND

**105. Athletics**  
SPRINT, HURDLE, JAVELIN, DISCUS, RELAY, MARATHON, VAULT, SHOTPUT, TRACK, STARTER

**106. Swimming**  
FREESTYLE, BUTTERFLY, BACKSTROKE, LENGTH, GOGGLES, DIVING, LANE, STROKE, POOL, FLIPTURN

**107. Olympics**  
MEDAL, TORCH, PODIUM, ANTHEM, RELAY, VILLAGE, OPENING, MASCOT, RECORD, CEREMONY

**108. Chess**  
BISHOP, KNIGHT, CASTLE, PAWN, GAMBIT, CHECKMATE, STALEMATE, OPENING, ENDGAME, BLUNDER

**109. Hockey**  
DRIBBLE, PENALTY, GOALIE, STICK, CORNER, TURF, TACKLE, FLICK, MIDFIELD, WHISTLE

**110. Badminton**  
SHUTTLE, RACQUET, SMASH, SERVICE, RALLY, NET, COURT, DROPSHOT, LOB, UMPIRE

**111. Cycling**  
PELOTON, SPRINT, CLIMB, JERSEY, TIMETRIAL, SADDLE, GEAR, DESCENT, CIRCUIT, DOMESTIQUE

**112. Motorsport**  
CIRCUIT, PITSTOP, CHICANE, FORMULA, OVERTAKE, TELEMETRY, APEX, PODIUM, QUALIFY, CHEQUERED

**113. Kitchen**  
LADLE, SKILLET, GRATER, COLANDER, SPATULA, KETTLE, CUTLERY, SIMMER, PANTRY, MORTAR

**114. Tools**  
HAMMER, WRENCH, PLIERS, CHISEL, DRILL, SANDER, CLAMP, MALLET, SPANNER, SCREWDRIVER

**115. Furniture**  
ARMCHAIR, DRESSER, OTTOMAN, BOOKCASE, WARDROBE, CABINET, STOOL, BENCH, MATTRESS, SIDEBOARD

**116. Clothing**  
JACKET, TROUSERS, SWEATER, BLOUSE, SCARF, MITTEN, WAISTCOAT, PYJAMAS, OVERALL, CARDIGAN

**117. Jewellery**  
NECKLACE, PENDANT, BANGLE, BROOCH, ANKLET, EMERALD, SAPPHIRE, PEARL, FILIGREE, LOCKET

**118. Stationery**  
NOTEBOOK, ERASER, RULER, ENVELOPE, CLIPBOARD, MARKER, SHARPENER, BINDER, POSTCARD, INKPOT

**119. Camping**  
TENT, LANTERN, CAMPFIRE, RUCKSACK, COMPASS, SLEEPING, SKEWER, TRAIL, CANTEEN, KINDLING

**120. Weather at Home**  
UMBRELLA, RAINCOAT, GALOSHES, SHUTTER, AWNING, HEATER, BLANKET, FIREPLACE, DRAUGHT, THERMOSTAT

**121. Colours**  
CRIMSON, AZURE, MAROON, OLIVE, INDIGO, AMBER, TURQUOISE, LILAC, SCARLET, EMERALD

**122. Shapes**  
TRIANGLE, HEXAGON, PENTAGON, OCTAGON, SPHERE, CYLINDER, PYRAMID, ELLIPSE, RHOMBUS, TRAPEZIUM

**123. Farming**  
HARVEST, PLOUGH, IRRIGATE, ORCHARD, SILO, TRACTOR, FURROW, GRANARY, SOWING, THRESHER

**124. Journalism**  
INTERVIEW, DEADLINE, SOURCE, BULLETIN, BROADCAST, EDITORIAL, STRINGER, NEWSROOM, DISPATCH, MASTHEAD

**125. Engineering**  
BLUEPRINT, GIRDER, SURVEY, TOLERANCE, CALIBRATE, PROTOTYPE, WELDING, BEARING, CONDUIT, SCAFFOLD

**126. Cooking Crafts**  
CHOPPING, MARINATE, GARNISH, SEASON, BRAISE, POACHING, REDUCE, FILLET, PLATING, TASTING

**127. Fishing**  
TRAWLER, HARPOON, NETTING, TACKLE, BAIT, REEL, ANGLER, CATCH, HARBOUR, LOBSTER

**128. Pottery**  
KILN, GLAZE, CLAY, WHEEL, TERRACOTTA, MOULD, FIRING, CERAMIC, SLIP, BURNISH

**129. Carpentry**  
TIMBER, PLANE, DOVETAIL, VARNISH, JOINERY, SAWDUST, MITRE, LATHE, VENEER, MORTISE

**130. Weaving**  
LOOM, SHUTTLE, WARP, WEFT, SPINDLE, YARN, TAPESTRY, THREAD, DYEING, PATTERN

**131. Seasons**  
SPRING, SUMMER, AUTUMN, WINTER, HARVEST, BLOSSOM, EQUINOX, SOLSTICE, FOLIAGE, THAW

**132. Time**  
CALENDAR, DECADE, CENTURY, MOMENT, INTERVAL, SUNDIAL, MIDNIGHT, DAWN, DUSK, ERA

**133. Light**  
BEAM, GLIMMER, RADIANCE, SHADOW, REFLECT, REFRACT, LANTERN, TWILIGHT, GLARE, PRISM

**134. Sound**  
ECHO, MURMUR, RESONANCE, WHISPER, CHIME, RHYTHM, SILENCE, TIMBRE, VOLUME, VIBRATION

**135. Travel**  
ITINERARY, PASSPORT, SUITCASE, JOURNEY, VOYAGE, EXPLORE, SOUVENIR, COMPASS, LODGING, DEPARTURE

**136. Games**  
MARBLES, DOMINO, PUZZLE, CHARADES, HOPSCOTCH, CAROM, LUDO, RIDDLE, TOKEN, SHUFFLE

**137. Books**  
PREFACE, GLOSSARY, INDEX, MARGIN, HARDBACK, PAPERBACK, SPINE, CHAPTER, AUTHOR, PUBLISH

**138. Weather Signs**  
RAINBOW, MIRAGE, HALO, DEWDROP, MIST, GUST, DOWNPOUR, CLOUDBURST, SUNSHINE, SHOWER

**139. Emotions**  
DELIGHT, WONDER, CURIOSITY, PATIENCE, COURAGE, SERENITY, GRATITUDE, EMPATHY, HOPEFUL, RELIEF

**140. Money**  
CURRENCY, BUDGET, SAVINGS, INVOICE, PROFIT, MARKET, TRADING, CAPITAL, RECEIPT, EXCHANGE
