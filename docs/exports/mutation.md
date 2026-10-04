# Mutation check: export builders

Tool: mutmut 3 (`uv run --with "mutmut>=3.2,<4" mutmut run`, configured in `pyproject.toml`), on
`src/exports/policy.py`, `numbers.py`, `citations.py`, `change_note.py` and `workspace.py`, against
`tests/exports/test_policy.py`, `test_pure_rules.py`, `test_workspace.py`, `test_mapping_edges.py`
and `test_mutation_kills.py`. Run on 2026-10-04 in a scratch copy of the repository.

| Run | Killed | Survived | What changed between runs |
| --- | --- | --- | --- |
| 1 | 516 of 667 | 151 | first run |
| 2 | 587 of 667 | 80 | exact violation tuples (where and row id), asymmetric change data, boundaries, error items |
| 3 | 613 of 667 | 54 | workspace caps inclusive at the boundary and naming the offender |
| 4 | 625 of 667 | 42 | `check_zip` checks every member after `RESTORE.md`, sums every member, entry attributes |

## The 42 survivors, reviewed (none changes behaviour a test can observe)

* 10 wrap a message in `XX...XX`: the asserted message is still a substring.
* 9 mutate the explicit name checks of `safe_name` (`/`, `\`, `..`, `:`): the strict name pattern
  refuses the same names; the explicit checks are defence in depth.
* 9 mutate zip entry details (`compress_type`, `compresslevel`, `external_attr` set to a falsy
  value, `date_time` omitted): `ZipFile.writestr` overrides them with the method passed and
  resets a falsy `external_attr` to `0o600 << 16`; an omitted `date_time` defaults to 1980-01-01,
  the configured entry time; a level change alters both builds identically.
* 3 change `zip(..., strict=...)` in `change_note._rows`: the lengths are checked just before.
* 2 change `json.dumps(default=...)`: the manifest holds no value JSON cannot encode.
* 4 change a strip set by adding `X` (numbers never contain `X`), or the separator a source code
  is replaced with before numbers are read (any non-digit separator is equivalent).
* 1 changes `len(text) <= limit` to `<` in `split_for_cell`: at equality the split yields the same
  single part.
* 4 change the 80-character truncation of a name inside a problem message to 81.

Survivor list (mutant :: changed line):

```
exports.policy.x_split_for_cell__mutmut_4 :: +        raise ValueError("XXlimit must be positiveXX")
exports.policy.x_split_for_cell__mutmut_6 :: +    if len(text) < limit:
exports.numbers.x_normal_form__mutmut_8 :: +    token = token.rstrip("XX%XX").replace(",", "")
exports.numbers.x_normal_form__mutmut_19 :: +    whole = whole.lstrip("XX0XX") or "0"
exports.numbers.x_normal_form__mutmut_24 :: +    frac = frac.rstrip("XX0XX")
exports.numbers.x_numbers_in__mutmut_6 :: +    stripped = _CODE.sub("XX XX", text)
exports.numbers.xǁNumberRegistryǁadd_sentence__mutmut_10 :: +        raise BannedContent("XXa sentence key was registered with two textsXX", (key,))
exports.numbers.xǁNumberRegistryǁadd__mutmut_19 :: +        raise BannedContent("XXa number key was registered with two valuesXX", (key,))
exports.change_note.x__rows__mutmut_16 :: +    return dict(zip(ids, digests, strict=None))
exports.change_note.x__rows__mutmut_19 :: +    return dict(zip(ids, digests, ))
exports.change_note.x__rows__mutmut_20 :: +    return dict(zip(ids, digests, strict=False))
exports.workspace.x_safe_name__mutmut_2 :: +        or ".." in name.split("/") and ":" in name
exports.workspace.x_safe_name__mutmut_3 :: +        or "\\" in name and ".." in name.split("/")
exports.workspace.x_safe_name__mutmut_4 :: +        or name.startswith("/") and "\\" in name
exports.workspace.x_safe_name__mutmut_10 :: +        or name.startswith("XX/XX")
exports.workspace.x_safe_name__mutmut_11 :: +        or "XX\\XX" in name
exports.workspace.x_safe_name__mutmut_13 :: +        or "XX..XX" in name.split("/")
exports.workspace.x_safe_name__mutmut_15 :: +        or ".." in name.split(None)
exports.workspace.x_safe_name__mutmut_16 :: +        or ".." in name.split("XX/XX")
exports.workspace.x_safe_name__mutmut_17 :: +        or "XX:XX" in name
exports.workspace.x_safe_name__mutmut_25 :: +        raise UnsafePackage("XXunsafe member nameXX", (name[:max_len],))
exports.workspace.x_safe_name__mutmut_32 :: +        raise UnsafePackage("XXan internal-only member cannot enter the workspaceXX", (name,))
exports.workspace.x_build_workspace__mutmut_10 :: +        raise UnsafePackage("XXthe workspace needs exactly the five client files and their dataXX")
exports.workspace.x_build_workspace__mutmut_32 :: +        raise UnsafePackage("XXduplicate member namesXX")
exports.workspace.x_build_workspace__mutmut_60 :: +    manifest_bytes = (json.dumps(document, indent=1, sort_keys=True, default=None) + "\n").encode()
exports.workspace.x_build_workspace__mutmut_64 :: +    manifest_bytes = (json.dumps(document, indent=1, sort_keys=True, ) + "\n").encode()
exports.workspace.x_build_workspace__mutmut_80 :: +        raise UnsafePackage("XXtoo many membersXX", (str(len(ordered)),))
exports.workspace.x_build_workspace__mutmut_90 :: +            raise UnsafePackage("XXmember over the size capXX", (name,))
exports.workspace.x_build_workspace__mutmut_99 :: +        raise UnsafePackage("XXpackage over the total size capXX", (str(total),))
exports.workspace.x_build_workspace__mutmut_119 :: +            info = zipfile.ZipInfo(name, )
exports.workspace.x_build_workspace__mutmut_120 :: +            info.compress_type = None
exports.workspace.x_build_workspace__mutmut_123 :: +            info.external_attr = None
exports.workspace.x_build_workspace__mutmut_124 :: +            info.external_attr = 0o600 >> 16
exports.workspace.x_build_workspace__mutmut_129 :: +            z.writestr(info, b, compress_type=None, compresslevel=6)
exports.workspace.x_build_workspace__mutmut_130 :: +            z.writestr(info, b, compress_type=method, compresslevel=None)
exports.workspace.x_build_workspace__mutmut_133 :: +            z.writestr(info, b, compresslevel=6)
exports.workspace.x_build_workspace__mutmut_134 :: +            z.writestr(info, b, compress_type=method, )
exports.workspace.x_build_workspace__mutmut_135 :: +            z.writestr(info, b, compress_type=method, compresslevel=7)
exports.workspace.x_check_zip__mutmut_22 :: +                problems.append(f"unsafe name {info.filename[:81]!r}")
exports.workspace.x_check_zip__mutmut_30 :: +                problems.append(f"symlink {info.filename[:81]!r}")
exports.workspace.x_check_zip__mutmut_33 :: +                problems.append(f"member over cap {info.filename[:81]!r}")
exports.workspace.x_check_zip__mutmut_70 :: +                    problems.append(f"hash mismatch {name[:81]!r}")
```
