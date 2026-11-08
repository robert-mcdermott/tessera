# Groups

Groups are how your organization is arranged: divisions, departments, programs, centers, shared resources, labs, and any other kind you name. Each group has people, with their roles and the dates they joined and left. A group can sit under several others; for example, a program can span two divisions.

You can curate groups by hand in the app, through the API, or from a file. Each group and membership records where it came from and the identifier it has there, so an export from your HR or organization system can supply groups later without creating duplicates.

## Kinds, roles, and what you call them

The `[vocabulary]` section of the configuration names the kinds of group you offer, the roles people hold in them, and what the app calls a group:

```toml
[vocabulary]
group = "Group"
groups = "Groups"
leader_roles = ["lead", "co_lead"]   # listed as a group's leaders
default_role = "member"              # for memberships that name no role

[vocabulary.kinds]
division = "Division"
program = "Program"
lab = "Lab"

[vocabulary.roles]
lead = "Lead"
member = "Member"
trainee = "Trainee"
```

Groups and memberships store the **key** (`lab`, `trainee`), and the app shows the **label**. You can rename a label at any time, but keep its key. See [Configuration](configuration.md#vocabulary) for the defaults.

## Curating groups in the app

**Admin → Groups** lists every group with its kind, the groups it sits within, its current members and alumni, and where it came from. Filter by kind or find a group by name or code. Archived groups are hidden unless you ask for them.

Open a group to see its members. **Current** lists the people who belong now, leaders first; **Alumni** lists the people whose memberships have ended. From a group's page you can:

- **Add member:** find a person in the directory, then choose their role, an optional title, and the dates. Dates can be as precise as you know them: `2015`, `2015-07`, or `2015-07-01`. Leave **Until** empty while the membership is current.
- **Edit** a membership to change its role, title, or dates. To record that someone left, set **Until**: they move to Alumni and stay there.
- **Remove** a membership only if it was a mistake. It disappears from the group, and later imports will not bring it back.
- **Edit** the group: its name, kind, short name, code, description, address, and the groups it sits within. A group can sit within any number of others, but never within a group inside itself.
- **Archive** a group that no longer exists. Its memberships and history stay, and you can restore it at any time.
- **Merge** a group into another: its members, the groups within it, its place under other groups, and curator roles limited to it all move to the group you choose. Where the same person holds the same role in both, the dates widen to cover both. The merged group's address leads to the group that remains. A merge cannot be undone from the app.

Every change, by hand or by import, is listed in the group's **History** with who made it and when.

A group's **address** (`/groups/okafor-lab`) is made from its name when it is created and does not change when the group is renamed, so links to it keep working. You can change it on purpose in the edit form.

## Importing groups from a file

```
uv run tessera groups import groups.csv --source hr
uv run tessera groups import memberships.csv --source hr
```

`--source` names where the file comes from. It can be any short name, such as `hr` or `registry`, but use the same name every time, because each row's identifier is unique only within its source. Importing the same source again updates the groups and memberships it made earlier.

### Groups

A CSV file with a `name` column is a list of groups:

| Column | Required | Meaning |
|---|---|---|
| `id` | recommended | The source's own identifier for the group |
| `name` | yes | The group's name |
| `kind` | no | A key from `[vocabulary.kinds]`; rows with any other kind are skipped |
| `parents` | no | The groups it sits within, separated by `;`: their identifiers in this source, codes, or addresses |
| `code` | no | Your organization's own code for the group |
| `short_name` | no | An abbreviation |
| `description` | no | What the group does |
| `status` | no | `active` or `archived` |
| `slug` | no | The address, used only when the group is created |

```csv
id,name,kind,parents
D10,Division of Neuroscience,division,
P20,Memory and Aging Program,program,D10;D30
L31,Sleep and Memory Lab,lab,P20
```

### Memberships

A CSV file with a `group` column is a list of memberships:

| Column | Required | Meaning |
|---|---|---|
| `group` | yes | The group's identifier in this source, its code, or its address |
| `person`, `orcid`, `email`, or `local_id` | one of them | Who the membership is for. People must already be in the directory: the roster adds people |
| `role` | no | A key from `[vocabulary.roles]`; the default role if empty |
| `title` | no | The person's title |
| `start`, `end` | no | `2015`, `2015-07`, or `2015-07-01` |
| `id` | recommended | The source's identifier for the membership, so a change of role updates the same membership |

### JSON

A JSON file holds both: `{"groups": [...], "memberships": [...]}`, with the same names as keys, and `parents` as a list.

### What an import changes

- The source is in charge of what it supplies. An import overwrites the fields its rows give, and replaces the parent links it made earlier. Links you added by hand stay.
- A row without an identifier matches a group by code, then by name. A group made by hand is **adopted** by the first import whose row matches it and carries an identifier: from then on it belongs to that source.
- A group merged by hand is still found: an import that names a merged group's identifier updates the group it was merged into.
- A membership you removed stays removed.
- `--complete` says the file is the source's whole list. Groups from this source that the file no longer lists are archived, and current memberships from this source that it no longer lists end today, so their people become alumni.
- `--dry-run` reports what would change and changes nothing.

Rows that cannot be imported are listed with the reason, and the rest are imported. The command exits with status 2 when it skips rows.

### The roster is one way in

The roster import (`tessera people import`) adds people, and the groups and memberships on its rows. A roster row can now give a group's kind (`kind`) and the person's role (`role`) as well as the group's name and code. Groups and memberships from the roster are recorded as coming from `roster`, so importing the roster again updates them.

## Through the API

Admins can do all of this through the API: `POST /api/v1/groups` creates a group, `POST /api/v1/groups/{group}` changes, nests, archives, or restores one, `POST /api/v1/groups/{group}/merge` merges it, and `/api/v1/groups/{group}/members` adds, changes, ends, and removes memberships. `POST /api/v1/groups/import?source=hr` takes the same rows as the JSON file, with `complete` and `dry_run`, so a scheduled job can feed groups from another system. Anyone signed in can read groups, members, and history. See [The API](api.md).

## Curator scopes

A curator can be limited to the people of one group (Admin → Accounts). The scope includes every group beneath it, through any of its parents, and only people whose memberships are current.
