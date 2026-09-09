[en English](../README.md) · [ru Русский](README.ru.md) · [es Español](README.es.md) · [pl Polski](README.pl.md) · [uk Українська](README.uk.md) · [de Deutsch](README.de.md) · [fr Français](README.fr.md) · [zh-CN 简体中文](README.zh-CN.md) · [ar العربية](README.ar.md) · [he עברית](README.he.md)

# paranoid-podman

`paranoid-podman` הוא מעטפת ניסיונית ברמת המשתמש עבור Podman במצב rootless
ועבור `podman-compose`. הוא מיועד למפתחים שמריצים לעיתים פקודות קונטיינר מתוך
פרויקטים שאינם נותנים בהם אמון מלא.

הפרויקט מוסיף מגבלות פשוטות וישירות נגד שגיאות נפוצות של יציאת קונטיינר
מהגבולות הצפויים וגישה ל-host, תוך שמירה על תהליכי פיתוח מקומיים רגילים. הוא אינו
אנטי-וירוס, מוצר אבטחה ארגוני או sandbox מלא.

> [!WARNING]
> זוהי הגנה בשכבות ולא הגנה מוחלטת. ייתכן שהיא לא תעצור חולשות zero-day,
> טכניקות תקיפה לא מוכרות, חולשות ב-kernel או ב-runtime, או תהליך שכבר קיבל
> גישה לחשבון המשתמש ב-host.

## תוכן

- [אודות](#about)
- [מה מוגן](#what-it-protects)
- [התקנה](#install)
- [שימוש](#usage)
- [DevPod](#devpod)
- [תאימות](#compatibility)
- [מגבלות](#limitations)
- [פיתוח](#development)
- [אבטחה](#security)
- [רישיון](#license)

<a id="about"></a>

## אודות

זרימת הפקודה היא:

```text
podman / compose command -> parser -> policy -> verified real provider
```

ארגומנטים בטוחים נשמרים. גישה מסוכנת ל-host נדחית, ברירות מחדל נבחרות להקשחה
נוספות, וקובצי פרויקט מוגנים מעוגנים לקריאה בלבד בתוך קונטיינרים חדשים.

התכנון ישיר במכוון:

- לחסום את הנתיבים הישירים ביותר להרשאות ולגישה ל-host;
- לשמור על פיתוח רגיל כל עוד הוא נשאר בגבולות הפרויקט;
- להעדיף מגבלות mount מפורשות וברורות על פני סורקי תוכן;
- להיכשל באופן סגור כאשר צורת פקודה רלוונטית לאבטחה אינה מוכרת; וכן
- לתעד בכנות את גבולות ההגנה הנותרים.

<a id="what-it-protects"></a>

## מה מוגן

| קלט או התנהגות | פעולה |
| --- | --- |
| privileged mode, ‏host/joined namespaces, ‏capabilities נוספות, התקנים או security options שרירותיות | דחייה |
| sockets של מנוע Podman/Docker ונתיבי runtime רגישים ב-host | דחייה |
| שורש מערכת הקבצים, כל תיקיית הבית, עצי מערכת רחבים או bind רחב יותר מהפרויקט | דחייה |
| קבצים ותיקיות רגילים בודדים שצוינו במפורש | אישור ו-canonicalization |
| אפשרויות bind שמשנות בעלות או labels ב-host, כגון `U`, ‏`idmap` או relabeling לא בטוח | דחייה |
| פורטים מפורסמים | שמירה על מיפויי TCP/UDP רגילים, כולל כל הממשקים וכתובות הרשת המקומית |
| credentials סביבתיים, נקודות קצה של desktop/session ומשתני routing של provider | הסרה או דחייה |
| הגדרות פרויקט מוגנות קיימות תחת bind שניתן לכתיבה | הוספת submounts לקריאה בלבד |
| `start`, ‏`exec` או lifecycle פעיל של Compose על קונטיינר ישן או זר | דחייה עד ליצירה מחדש בידי ההתקנה והמדיניות הנוכחיות |
| הגדרות Compose | פענוח, בדיקה, כתיבה מחדש והרצת עותק פרטי שנבדק |
| literal secrets ברורים ב-Compose או בהשמות Dockerfile | דחייה תוך הסתרת הערכים |
| credentials בשורש build context שלא הוחרגו בקובץ ignore | דחייה לפני הרצת builder |

פקודות `run` ו-`create` ישירות מקבלות גם ברירות מחדל תואמות כגון
`no-new-privileges`, מגבלת PID, ביטול העברה אוטומטית של proxy, מדיניות restart
לא מתמשכת וללא image pull משתמע. אפשרויות runtime לא מוכרות אינן מועברות.

### הגדרות פרויקט מוגנות

נתיבים שהתגלו בשמות הבאים זמינים לקריאה בלבד בתוך קונטיינרים חדשים:

- `.devcontainer`, `.devcontainer.json`, ‏`devcontainer.json`;
- `.dockerignore`, ‏`.containerignore`;
- קובצי dotenv פעילים וקובצי הגדרות credentials נפוצים; וכן
- `.git`, ‏`.gitmodules`, ‏`.git-credentials`.

נתיבים חסרים אינם נוצרים. תוכן devcontainer אינו נסרק או משוכתב: ההגנה היא
כלל mount של מערכת הקבצים. קובצי Dockerfile מקבלים רק את בדיקת ה-literal secrets
הקטנה המתוארת בהמשך.

ניתן לערוך קובצי Dockerfile,‏ Containerfile ו-Compose מחוץ ל-`.devcontainer` מתוך
הקונטיינר. כל התוכן של `.devcontainer` נשאר לקריאה בלבד. יש ליצור מחדש קונטיינרים
קיימים כדי להחיל את הרשאות המיפוי החדשות.

תיקיות שאינן ניתנות לקריאה ושייכות ל-UID אחר, כגון נתוני מסד נתונים rootless,
ממופות ללא סריקת התוכן או שינוי הרשאות. הקבצים שבתוכן אינם מוגנים אוטומטית
מפני כתיבה.

`.git` הוא לקריאה בלבד כברירת מחדל. ניתן להשאירו ניתן לכתיבה עבור הפעלה אחת בלי
להחליש את שאר הנתיבים המוגנים:

```bash
PODMAN_GUARD_PROTECT_GIT=0 podman compose up
```

### בדיקת Compose

מתאם Compose:

1. מבצע בדיקה מוקדמת לקובצי המקור ומרנדר את ההגדרות לאחר resolve;
2. בודק גישה ל-host, ‏namespaces, ‏mounts, משאבים ושדות נתמכים;
3. כותב snapshot פרטי במצב `0600` לאחר השלמת interpolation;
4. מריץ רק את ה-snapshot שנבדק.

מיפויים בתוך הפרויקט נשארים שקטים. לפני `up` או `run`, גישה לקובץ או לתיקייה
במארח מחוץ לפרויקט דורשת אישור: `[warning]` כתום מציג את הנתיב המלא, השירות, מצב
הגישה וקובץ Compose המקורי עם מספר השורה. יש להקליד בדיוק `y` ואז Enter לאישור
המיפויים עבור פקודה זו. Enter בלבד, תשובה אחרת או EOF מבטלים; ללא קלט מטרמינל
הפעולה נחסמת בלי לקרוא stdin. הבחירה אינה נשמרת. פקודות בדיקה, הסרה ועבודה עם
קונטיינרים קיימים אינן מבקשות אישור. אי אפשר לאשר כך נתיבים או sockets אסורים.

`PODMAN_GUARD_DEBUG=1` מציג סיכום ללא ערכים רגישים. אותן בדיקות חלות על טרמינל
ואוטומציה. דחיות מודגשות בכותרת `BLOCKED`,‏ `UNSUPPORTED` או `ERROR`, עם הסיבה ושם
השירות הרלוונטי אם הוא ידוע.

אבחון Compose אינו מדפיס ערכי environment לאחר resolve או שגיאות provider
גולמיות. בדיקת literal secrets קטנה ומבוססת שמות במכוון; היא אינה סורק secrets
כללי.

קונטיינרים ישירים וקונטיינרי Compose חדשים מקבלים שני provenance labels שמורים:
דור המדיניות ומזהה אקראי של ההתקנה המקומית. פעולות שיכולות להתחיל, לחדש או לבצע
קוד דורשות התאמה של שני ה-labels לפני שימוש בקונטיינר קיים. כך image אינו יכול
לעבור את הבדיקה רק באמצעות הצהרה מראש על policy label הציבורי. Inspection לקריאה
בלבד ו-cleanup נשארים זמינים לאבחון ולהסרה בטוחה של קונטיינרים ישנים.

הממשקים המדויקים מתוארים ב
[מדיניות Podman הישירה](../docs/direct-policy.md),
[מדיניות Compose](../docs/compose-policy.md) וב
[מודל האיומים](../docs/threat-model.md).

<a id="install"></a>

## התקנה

נדרשים Linux עם Podman במצב rootless בגרסה 6.1.x,‏ `podman-compose` בגרסה
1.6.x,‏ Python 3.10 ומעלה עם תמיכה בסביבות וירטואליות ו־Bash. לשימוש ב־DevPod
נדרשים גם DevPod וכלי לקוח OpenSSH. גרסת DevPod 0.6.15 נבדקה; גם גרסאות
אחרות מתקבלות. מתוך המאגר, בחשבון המשתמש הרגיל:

```bash
./install.sh install
```

אפשר להריץ `./install.sh` ללא ארגומנטים כדי להציג מצב ותפריט:
`install` להתקנה חדשה, או `update` / `uninstall` להתקנה קיימת. Enter
יוצא ללא שינויים. ללא מסוף אינטראקטיבי מוצגים רק המצב והפעולות הזמינות;
בסקריפטים יש לציין את הפעולה במפורש.

`./install.sh --help` מתאר את האפשרויות לפי פעולה. אפשרויות הספקים וקובצי
wheel חלות על `install` / `update`; האפשרויות `--without-devpod`
ו־`--backup-existing` חלות רק על `install`, ו־`--devpod-ssh-config` רק על
`uninstall`. התקנה רגילה אינה דורשת אפשרויות נוספות.

המתקין מוצא את Podman,‏ Compose וגם DevPod אם הוא מותקן, מוריד כלי Python
ותלויות, בונה את היישום ומתקין אותו בסביבה פרטית. אין צורך להכין חבילות,
ערכי בדיקה או סביבות Python בעצמכם. פקודות קיימות מגובות לאחר אישור ומשוחזרות
בעת ההסרה.

תצוגה מקדימה ללא הורדות או שינוי קבצים:

```bash
./install.sh install --dry-run
```

### מיקום הפקודות

תיקיית הפקודות הרגילה היא `${XDG_BIN_HOME:-$HOME/.local/bin}`.
הציבו אותה בראש `PATH` כדי להשתמש בפקודות המוגנות:

```bash
export PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$PATH"
command -v podman docker compose-guard
```

אם התיקייה עדיין אינה ראשונה, הוסיפו את שורת `export` להגדרות המעטפת.
היישום נמצא ב־`${XDG_DATA_HOME:-$HOME/.local/share}/paranoid-podman`.

### עדכון או הסרה

הריצו מתוך המאגר שמכיל את גרסת היישום הרצויה:

```bash
./install.sh update
./install.sh status
```

הסרה ושחזור הפקודות שנשמרו:

```bash
./install.sh uninstall
```

הריצו `./uninstall.sh` לבדיקת המצב ולהצגת תפריט `uninstall` / `Exit`.
Enter יוצא ללא שינויים; ללא מסוף אינטראקטיבי מוצג מידע בלבד.
`./uninstall.sh --help` מתאר את אפשרויות ההסרה, ו־
`./uninstall.sh --dry-run` מציג תצוגה מקדימה לאחר בחירת ההסרה בתפריט.

`--dry-run` פועל גם בעדכון ובהסרה.
קובצי התקנה ששונו מדווחים במקום להימחק. כדי להוסיף DevPod להתקנה שלא כללה אותו,
הסירו את היישום והתקינו שוב לאחר ש־DevPod זמין. לאחר מכן יש ליצור מחדש את
הקונטיינרים המוגנים.

### הגדרות אופציונליות

`--without-devpod` מתקין רק הגנות Podman ו־Compose. האפשרות `--devpod PATH`
בוחרת קובץ הפעלה מחוץ ל־`PATH`; האפשרויות `--podman` ו־`--compose-provider`
מחליפות את האיתור האוטומטי. `--backup-existing` מאפשר גיבוי ללא אישור אינטראקטיבי.
לתיקיות מותאמות השתמשו בערכי `--bindir` ו־`--libdir` עקביים.

הגדרות SSH בתיקיית DevPod הרגילה משוחזרות אוטומטית. עבור הקשרים תחת
`DEVPOD_HOME` אחר, ציינו כל נתיב מותאם של הגדרות SSH:

```bash
./install.sh uninstall --devpod-ssh-config /absolute/path/to/ssh-config
```

[המדריך המתקדם להתקנה ללא רשת](../docs/wheel-packaging.md) מתאר חבילות שהוכנו
מראש. התקנה רגילה אינה דורשת את האפשרויות האלה.

<a id="usage"></a>

## שימוש

הריצו את הפקודות מתוך תיקיית פרויקט קיימת. החליפו את
`localhost/my-dev-image:latest` בתמונת קונטיינר שכבר קיימת מקומית: הפעלה ישירה
משתמשת ב־`--pull=never`. Compose דורש קובץ הגדרות ושירות בשם `app`.

```bash
podman run --rm -v .:/workspace localhost/my-dev-image:latest
podman compose up
podman compose ps
podman compose exec app sh
podman compose run --rm --build app
podman compose down
```

משימות Compose חד־פעמיות תומכות בפרופילים, ב־`--no-deps` ובאפשרויות סביבה, משתמש, תיקיית
עבודה ופורטים. `run --build` נעצר אם הבנייה נכשלת. נתמכים `up --force-recreate` ואיפוס
מפורש באמצעות `down --volumes --remove-orphans`; האחרון מוחק את הוולומים של הפרויקט.

ה-aliases ‏`docker`, ‏`podman-compose` ו-`docker-compose` מותקנים עבור תהליכים
תואמים. פקודות מחוץ לממשק שנבדק נדחות; לצורך ניהול ה-host יש להפעיל במכוון את
קובץ Podman האמיתי.

דחיות המדיניות של Podman ישיר ושל Compose מסתיימות בקוד 125 ומציגות את הסיבה
ושורת `next step:` ללא ערכים סודיים.
שגיאות build context מציגות את כל הנתיבים שנמצאו
בבת אחת. יש להחריג קובצי dotenv פעילים ונתיבי אישורים נפוצים מקלטי ה-build
המועתקים באמצעות `.containerignore` או `.dockerignore`. `.git` מותר ב-build
context ונשאר זמין ב-runtime workspace, שבו ה-guard מגן עליו כברירת מחדל
באמצעות mount נפרד במצב read-only.

הרצות ישירות מוגנות משתמשות ב-`--pull=never`, ולכן ה-image חייב להיות קיים.
ל-DevPod יש צורות image pull/build lifecycle שנבדקו באופן מצומצם.

לבדיקה ולתיקון של ההחרגות בהקשר הבנייה:

```bash
paranoid-podman build-context audit .
paranoid-podman build-context protect .
```

`protect` מאפשר בחירת נתיבים; `--all` מחיל את כל ההחרגות שנמצאו ללא שאלה.
רק קובץ ignore הרגיל משתנה, בלי לעקוף את המדיניות.

<a id="devpod"></a>

## DevPod

קונטיינרים חדשים של DevPod משתמשים בשם ה־workspace בתור hostname. hostname שהוגדר במפורש
נשמר; לשמות שמקוצרים או מנורמלים מתווסף hash קצר. כדי להחיל זאת על קונטיינרים קיימים יש
ליצור אותם מחדש.

המעטפת מטפלת בצורות הפקודות שנבדקו של מנהלי ההתקן Docker ו־Compose
ב־DevPod 0.6.15, ובהן:

- גילוי קונטיינר, inspect, start, stop, logs, exec והסרה;
- image inspect, pull, build, tag ו-push;
- תהליך ההעתקה הפרטי להגדרת `/etc/passwd` ו-`/etc/group`;
- חיפוש פרויקט Compose, שמות פרויקט, קובץ `.env` של הפרויקט וקובצי override
  שנוצרו; וכן
- פעולות Compose build, up, stop ו-down.

DevPod אינו עוקף את מדיניות היצירה המשותפת. Workspace שמבקש privileged mode,
‏host namespaces, ‏engine sockets, ‏capabilities מסוכנות או host mount רחב עדיין
נדחה.

Read-only submounts ו-provenance labels מוחלים בזמן יצירת הקונטיינר. לאחר התקנת
המנגנון, יש ליצור מחדש DevPod workspace ישן לפני הפעלה או כניסה דרך ה-wrapper.
כך גם לאחר התקנה מחדש, משום שהיא מקבלת מזהה התקנה חדש. פקודות cleanup נשארות
זמינות. כדי להשאיר את `.git` ניתן לכתיבה, הפעילו את DevPod עם
`PODMAN_GUARD_PROTECT_GIT=0`.

### בידוד פרטי גישה של SSH

DevPod בוחר את סביבת הפיתוח IDE לפי ברירות המחדל, הגדרות סביבת העבודה או
האפשרות `--ide`. המעטפת אינה מחייבת עורך מסוים. פתיחה וחיבור מחדש במצב IDE-only
נבדקו ידנית ב־Codium; סביבות פיתוח אחרות והתהליך המלא של מנהל ההתקן Compose
עדיין דורשים בדיקות קבלה. בעיית משך חיים של שקע סוכן הפרויקט נצפתה בשילוב
Codium ו־Open Remote - SSH 0.1.2:
[DEV-001](../KNOWN_ISSUES.md#dev-001-vscode-loses-the-project-ssh-agent-socket).
חיבורי OpenSSH ו־`devpod ssh` חדשים נבדקו עם מפתח פרויקט אחד.

בתפריט האינטראקטיבי: **1** יצירת מפתח פרויקט, **2** IDE ללא פרטי גישה של המארח,
**3** בחירת מפתח פרויקט קיים, **4** חשיפת סוכן המארח המלא פעם אחת לאחר אישור מדויק
`y`,‏ **5** ביטול. Enter בוחר באפשרות 1; למצב IDE-only בחרו במפורש **2**.

מצב הפרויקט מפעיל `ssh-agent` ייעודי עם זהות מאומתת אחת בלבד. המפתח הפרטי אינו
מועתק או ממופה לתוך הקונטיינר, אך קוד בסביבת העבודה יכול לבקש מהסוכן לחתום
באמצעותו. יש להגביל את המפתח הציבורי למאגר יחיד ב-GitHub,‏ GitLab או Gitea.

מצבים מוגנים משביתים הזרקה אוטומטית של פרטי Git ו-registry,‏ GPG-agent ומפתח
חתימת SSH בהקשר DevPod שנבחר. `devpod build` עצמאי ללא מצב מוגדר אינו מבקש
בחירה אינטראקטיבית ואינו מעביר פרטי מארח אל ה-workspace.

כדי למנוע מ-DevPod לפתוח את ה-IDE לפני הגנת בלוק ה-SSH, העטיפה יוצרת תחילה את
סביבת העבודה עם `--open-ide=false`, מגבילה את הבלוק ולאחר מכן פותחת את ה-IDE
בלי ליצור מחדש את הקונטיינר ובלי לכתוב שוב את הגדרות ה-SSH.

המפתח הפרטי הקיים חייב להיות ייעודי לפרויקט, ואסור לשמור אותו ישירות בתוך
`~/.ssh`; מותר לשמור אותו בתיקיית משנה.

```bash
paranoid-podman devpod audit WORKSPACE
paranoid-podman devpod configure WORKSPACE
paranoid-podman devpod key show WORKSPACE
paranoid-podman devpod key stop WORKSPACE
```

המצב נגזר מבלוק ה-SSH של DevPod ומהמפתחות תחת
`~/.ssh/paranoid-podman/`; לא נוצר קובץ מדיניות נפרד. פרטים:
[בידוד פרטי DevPod](../docs/devpod-credentials.md) (באנגלית).

החליפו את `WORKSPACE` במזהה סביבת עבודה קיימת. השתמשו בערכים תואמים עבור
`--context`,‏ `--devpod-home` ו־`--ssh-config` לפי הצורך. בפרויקט מקומי חדש, התיקייה
שמעבירים ל־`devpod up` חייבת להתקיים; אחרת היא עלולה להתפרש ככתובת מאגר.
בחרו IDE-only בזמן ה־`up` האינטראקטיבי הראשון: `configure` לא יכול לשמור את הבחירה
לפני יצירת בלוק ה־SSH של DevPod. ראו את
[הדוגמה המקומית](../docs/devpod-credentials.md#open-a-local-workspace).

מצבים מוגנים משביתים גם חיפוש אוטומטי של מפתחות פרטיים. הגדרות ההקשר משפיעות
על כל סביבות העבודה השייכות אליו.

<a id="compatibility"></a>

## תאימות

| רכיב | תאימות |
| --- | --- |
| מערכת הפעלה | Linux |
| Python | 3.10 ומעלה |
| Podman | Rootless 6.1.x |
| Compose provider | `podman-compose` 1.6.x דרך `podman compose` |
| DevPod | נבדק עם 0.6.15; גרסאות אחרות מתקבלות; פקודות Docker/Compose שנבדקו ומגבלות IDE מפורטות למעלה |
| Docker Compose v2 | לא נתמך |

ה-installer דוחה סדרת major/minor של Podman או Compose שלא נבדקה. ה-wrappers
תלויים בהתנהגות ה-CLI של ה-provider, ולכן כל סדרה חדשה דורשת סקירה ובדיקות תאימות.

התאימות של גרסאות DevPod אחרות עדיין לא נבדקה. בדיקות הגישה לפרטי הזדהות
ולפקודות המותרות ממשיכות לפעול.

<a id="limitations"></a>

## מגבלות

- בדיקת ignore דורשת החרגה מלאה של נתיבים רגישים בשורש. חריגים שמכלילים מחדש
  קבצים בתיקיות משנה או דפוסים שלא נבדקו עשויים לדרוש כלל החרגה מפורש בסוף.
  הקשרי תמונה נוספים דורשים קידומת תעבורה מפורשת. ראו
  [מגבלות הבנייה](../docs/direct-policy.md#build-boundary).
- הסוכן יכול לחתום בזהות הפרויקט בלי לחשוף את המפתח הפרטי. שרת Git חייב לאכוף
  את גבולות הגישה למאגר.
- Wrapper ב-`PATH` אינו sandbox. תהליך host שרץ כמשתמש שלכם יכול להפעיל את
  Podman האמיתי או לגשת ישירות לאותם קבצים.
- ה-wrapper אינו יכול להגן מפני חולשות kernel, ‏Podman, ‏OCI runtime, ‏image,
  ‏parser או zero-day.
- רשת קונטיינר רגילה אינה sandbox לתעבורה יוצאת.
- Build יכול להריץ הוראות image שרירותיות ולקרוא כל נתיב context שלא הוחרג
  באמצעות `.containerignore` או `.dockerignore`. ה-guard בודק רק נתיבי credentials
  נפוצים בשורש והשמות literal ברורות.
- קונטיינרים שנוצרו בעבר או שכבר רצים אינם מקבלים הגנות mount חדשות. פקודות
  lifecycle מוגנות פעילות דוחות קונטיינרים ללא provenance המדיניות וההתקנה
  הנוכחיות.
- Provenance labels הם סמני תאימות מקומיים, לא חתימות קריפטוגרפיות. תהליך שכבר
  רץ כמשתמש ה-host יכול לקרוא, להעתיק או לעקוף אותם. הרצת guards ישירות מעץ
  המקור ללא installation ID מפורש משתמשת בערך פיתוח דטרמיניסטי; השתמשו ב-installer
  עבור provenance ייחודית להתקנה.
- קבצים יכולים להשתנות בין הבדיקה לבין הפעלת הספק.
- הממשק הנתמך קטן בכוונה מממשקי Podman ו-Compose המלאים.

כאשר נדרש בידוד חזק יותר, השתמשו ב-VM זמנית או בחשבון נפרד בעל הרשאות נמוכות.

<a id="development"></a>

## פיתוח

בדיקות מקומיות מהירות:

```bash
scripts/test.sh
scripts/check.sh syntax
```

הבדיקות המקומיות משתמשות בספקים מדומים ובתיקיות home/config זמניות; דגלי אינטגרציה שהתקבלו מהסביבה מושבתים. ברירת המחדל של `scripts/check.sh` היא `local`, עם Ruff, mypy, ‏Bandit, ‏ShellCheck, ‏`zizmor` ללא רשת וסריקת מקור ב-Gitleaks תוך הסתרת סודות. `scripts/audit.sh dependencies` מפעיל במפורש ביקורת רשת; `scripts/check.sh all` מוסיף אותה בלי להפעיל אינטגרציות אמיתיות.

`scripts/format.sh` מחיל עיצוב. יש להכין את הכלים בנפרד לפי [CONTRIBUTING.md](../CONTRIBUTING.md). CI מפריד בין בדיקות, ניתוח סטטי, סודות וביקורת תלויות.

המימוש נמצא ב־`src/paranoid_podman`. ראו את [מפת הקוד](../docs/architecture.md) ואת [הוראות בנייה והתקנה לא מקוונת של wheel](../docs/wheel-packaging.md).

| תיקייה | תפקיד |
| --- | --- |
| `src/paranoid_podman/` | קוד היישום |
| `bin/` | משגרים קטנים להפעלה מהמקור ולבדיקות |
| `dist/` | wheel וערך בדיקה שנוצרו בבנייה |

המתקין יוצר בנפרד את הפקודות ב־`--bindir`, והן משתמשות בסביבת Python הפרטית.
`bin/` נשאר חלק מעץ המקור.

`scripts/test.sh compose-provider` בוחר בבדיקה עם ספק Compose אמיתי. היא עשויה לתשאל את Podman, אך אינה מפעילה קונטיינרים; יש להשתמש בסביבה זמנית עם הגרסאות שנבדקו.
הבדיקות כוללות גם תרחישים יומיומיים עם Compose אמיתי ותחליף למנוע שרושם קריאות; הן נוספו
ל־CI.

חבילת ה-runtime האופציונלית דורשת image מקומי קיים עם `sh` ו-`sleep`. היא לעולם
אינה מושכת image ומשתמשת רק בקונטיינרים זמניים בעלי שמות ייחודיים:

```bash
PARANOID_PODMAN_TEST_IMAGE=docker.io/library/alpine:latest \
  scripts/test.sh rootless
```

הריצו אותה רק עם גרסאות Podman ו-Compose שנבדקו, בחשבון rootless זמני ללא
קונטיינרים או credentials חשובים.

בדיקת סוכן SSH יוצרת מפתח ושקע זמניים ואינה קוראת מפתחות SSH רגילים:

```bash
scripts/test.sh ssh-agent
```

העבודה שנותרה נמצאת ב-[TODO.md](../TODO.md), והנחיות לתרומה נמצאות ב
[CONTRIBUTING.md](../CONTRIBUTING.md).

<a id="security"></a>

## אבטחה

דווחו על חולשות חשודות לפי התהליך ב-[SECURITY.md](../SECURITY.md). אל תפרסמו
פרטי גישה פעילים, נתוני פרויקט פרטיים או פרטי ניצול חולשה בדיווח ציבורי.

<a id="license"></a>

## רישיון

[MIT](../LICENSE)
