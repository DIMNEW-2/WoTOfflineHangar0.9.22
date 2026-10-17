# WoTOfflineHangar0.9.22
Based on creations of Imperator Glekobes

Hello, dear developers and players!

The code below represents a standard Offline Hangar modification for the game version 0.9.22. It will allow you to view vehicles, modules, the old interface, etc. This modification is non-commercial, and all rights to the "World of Tanks" trademark and game resources belong to Wargaming.net or Lesta Games. The author of the project makes no claim to the intellectual property of these companies.

![Описание изображения](shot_014.jpg)

The package ships as readable Python sources (`*.py`) plus ready-to-use compiled modules (`*.pyc`, Python 2.7 bytecode, magic 62211). To rebuild the `.pyc` files yourself, use any [Python 2.7](https://www.python.org/downloads/release/python-2718/) (`python -m py_compile <file>`); to run inside the game you can also extract two files from [this archive](https://k2mg.net/res/storage/python26.7z) into your PjOrion folder and change your Python library to python27.dll via Terminal -> Settings -> Basic setup.
________________________________________
[RU] Новогодний ивент «Новогоднее наступление»

В состав мода входит полное оффлайн-восстановление новогоднего ивента «Новогоднее наступление» (NY 2018, токены `ny18:*`) из патча 0.9.22 — без боёв и без обращения к серверу:
- новогодняя сцена-ангар (`h07_newyear_2015`) с ёлкой, снеговиком, домом и гирляндами;
- размещение игрушек на слотах, фильтры, пометка «новых», коллекции 4 стилей;
- крафт игрушек за «хлопушки» и разбор игрушек на фрагменты;
- подарочные коробки (все 9 видов, включая «подарки от друзей») с анимацией открытия и лентой наград;
- 10 уровней атмосферы, сундуки уровня, экран наград;
- персональные скидки на танки (goodies 12000–12899) и найм девушек-танкисток (включая выбор имени/иконки);
- награды за коллекции (`ny18:cr:*`).

Реализация — `res_mods/0.9.22.0.1/scripts/client/Offline/NewYearEvent.py`: локальная (клиентская) обработка команд `CMD_NEW_YEAR_*` в том же контракте callback'ов, что и у настоящего сервера (включая 4-аргументный `onCmdResponseExt` c `ext`-данными). Состояние события хранится в штатном `ClientNewYear` аккаунта и передаётся через его `synchronize()`; скрытые квесты коробок/сундуков/коллекций лежат в `eventsData[EVENT_CLIENT_DATA.QUEST]`. Бои не требуются: при первом входе в ангар выдаётся стартовый набор (1000 фрагментов + коробки), а новые коробки/скидки/танкистки приходят за уровни атмосферы. Модуль подключается автоматически через `Offline/__init__.py`.

[EN] New Year event «Новогоднее наступление» (offline restoration)

The mod includes a complete offline restoration of the 0.9.22 New Year event (NY 2018, `ny18:*` tokens) — no battles and no server contact required: the festive hangar scene, toy placement/crafting/recycling, all 9 gift box types with the opening animation and reward ribbon, 10 atmosphere levels with level chests and the awards screen, personal vehicle discounts and tankwoman recruiting, and collection rewards. It is implemented in `Offline/NewYearEvent.py` as local (client-side) handling of the `CMD_NEW_YEAR_*` commands with the exact callback contract of the vanilla client (including the 4-argument `onCmdResponseExt` with unpickled `ext` data). Event state lives in the account's own `ClientNewYear` cache and is pushed through its `synchronize()`; hidden quests for boxes/chests/collections are stored in `eventsData[EVENT_CLIENT_DATA.QUEST]`. Since battles are not available offline, a starter kit (1000 craft fragments + gift boxes) is granted on first entry into the hangar. The module is mounted automatically via `Offline/__init__.py`.
________________________________________
[RU] Отказ от ответственности

Данный проект является некоммерческой модификацией для устаревшей версии игры World of Tanks (0.9.22), созданной исключительно в ознакомительных и исследовательских целях.
Важные положения:
1.	Этот проект не содержит серверного ПО (BigWorld Server), не эмулирует сетевые протоколы для многопользовательской игры и не позволяет обходить систему авторизации официальных серверов. 
2.	Весь функционал реализован через перехват локальных вызовов клиента (Client-side mocking). Изменения в LoginView.py позволяют перенаправить логику входа на локальный скрипт Manager.py. 
3.	Отображаемые показатели (золото, кредиты, опыт) являются локальными переменными внутри скрипта и существуют только в рамках текущей сессии оффлайн-режима. Они не имеют связи с реальными аккаунтами или базами данных правообладателей. 
4.	Репозиторий содержит только исходный код модификации на языке Python. Он не включает в себя оригинальные бинарные файлы игры (.exe), текстуры, модели, звуки или иные защищенные авторским правом ресурсы.
5.	Все права на торговую марку "World of Tanks", а также на игровые ресурсы принадлежат компаниям Wargaming.net и/или Lesta Games. Автор проекта не претендует на интеллектуальную собственность указанных компаний.
________________________________________
[EN] Disclaimer

This project is a non-commercial modification for the legacy version of World of Tanks (0.9.22), created solely for educational and research purposes.
Key Points:
1.	This project does not contain server-side software (BigWorld Server), does not emulate network protocols for multiplayer, and does not bypass official server authentication. 
2.	All functionality is implemented by mocking client-side calls. Modifications in LoginView.py redirect the login flow to the local Manager.py script. 
3.	All displayed stats (gold, credits, XP) are local variables within the script and only exist during the offline session. They have no connection to real accounts or official databases. 
4.	This repository contains only the Python source code for the modification. It does not include original game binaries (.exe), textures, models, sounds, or any other copyrighted material.
5.	All rights to the "World of Tanks" trademark and game assets belong to Wargaming.net and/or Lesta Games. The author of this project makes no claim to the intellectual property of these companies.

