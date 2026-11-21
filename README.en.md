# D-Link DGS-3627G — forgotten password recovery

[Русский](README.md) · [English](README.en.md)

A small tool and a step-by-step guide for recovering access to a D-Link
DGS-3627G (DGS-3600 series) when the login/password is unknown and the usual
reset methods do not work.

> **Important.** Use this only on hardware you own or on a device you are
> explicitly authorized to service. Changing authentication or SNMP settings
> can lock you out of the switch.

## Symptoms this guide helps with

- Console (`/dev/ttyUSB0`, 115200 8N1) and telnet reject `admin` with any
  known password.
- After 3 failed attempts the switch blocks login for about 60 seconds.
- Telnet prints something like this instead of a plain `Fail!`:

  ```text
  Timeout or some unexpected error happened on server host(<RADIUS_IP>/RADIUS)!
  The next server host or next method will be used to do authentication, if any.
  Error username or password, RADIUS can not accept NULL string!
  ```

- SNMP community `public` can read `sysDescr`, while `private` (or another
  RW community) can write.
- HTTP/HTTPS are closed; only port 23 (telnet) is open.
- This firmware has no Reset button and no password-recovery mode.

## What you need

- USB-UART console cable and access to the serial port (for diagnostics).
- A PC in the same network as the switch.
- net-snmp tools: `snmpget`, `snmpwalk`, `snmpset`
  (`sudo apt install snmp`).
- Python 3 for `switch_telnet.py` (no external libraries required).

Tested on DGS-3627G, firmware `Build 3.03.B04`, Boot PROM `1.10-B10`,
H/W `A1`. The OIDs come from the DGS-3600 Series MIB archive (`2.80.B31`).
Other firmware versions may differ — always read current values before
changing anything.

## Quick start

Once access is restored:

```bash
python3 switch_telnet.py
python3 switch_telnet.py 'show account' 'show ports 1-5'
python3 switch_telnet.py -i
python3 switch_telnet.py --host <SWITCH_IP> -u admin -p admin 'show switch'
python3 switch_telnet.py --script commands.txt
```

Defaults are `10.24.54.1`, `admin` / `admin`. The script logs in automatically,
answers `a` to paged CLI output and strips ANSI escape codes.

`--script` format: one command per line; empty lines and lines starting with
`#` are ignored.

---

## Recovery walkthrough

### 0. Connect

Plug the PC directly into a free switch port. If the IP is known, assign an
address from the same subnet:

```bash
sudo ip link set eth0 up
sudo ip addr add 10.24.54.100/24 dev eth0
ping -c 1 <SWITCH_IP>
```

### 1. Find the current IP via LLDP

The sticker on the case may show an old address. The switch announces its
current management IP in LLDP frames:

```bash
sudo tcpdump -i eth0 -s 0 -vvv ether proto 0x88cc
```

Look for `Management Address`. If `lldp` is installed:

```bash
sudo lldpcli show neighbors
```

The LLDP MAC should match the MAC printed during console boot.

### 2. Check open services

```bash
nc -vz <SWITCH_IP> 23
nc -vz <SWITCH_IP> 80
```

Usually only telnet is open. The banner can be read with:

```bash
nc <SWITCH_IP> 23
```

### 3. Diagnosis: RADIUS is the problem, not the password

An `admin`/`admin` attempt waits about 30 seconds and then reports a RADIUS
timeout. The switch asks an external authentication server first and never
reaches the local accounts.

Verify that write access works (writing the same value is harmless):

```bash
snmpget -v2c -c public <SWITCH_IP> 1.3.6.1.2.1.1.4.0
snmpset -v2c -c private <SWITCH_IP> 1.3.6.1.2.1.1.4.0 s "same value"
```

`snmpset` must complete without an error — that means writing is allowed.
If `v2c` fails, try `-v1`.

### 4. Read the login method lists

D-Link uses a **method list** — an ordered list of authentication methods.
Find out which list is used:

```bash
snmpget -v2c -c private <SWITCH_IP> 1.3.6.1.4.1.171.12.5.7.1.2.0
snmpget -v2c -c private <SWITCH_IP> 1.3.6.1.4.1.171.12.5.7.1.1.0
```

- `...7.1.2.0` — telnet login (`tac_login` in our case)
- `...7.1.1.0` — console (`default`)

Read the lists themselves:

```bash
snmpwalk -v2c -c private <SWITCH_IP> 1.3.6.1.4.1.171.12.5.3
snmpwalk -v2c -c private <SWITCH_IP> 1.3.6.1.4.1.171.12.5.4
```

Column `2` is the first method (`Method1Type`). For `tac_login` it was `4`
(radius); for `default` it was `6` (local).

The row index is the name length followed by its ASCII codes. For
`tac_login`:

```bash
python3 -c "s='tac_login'; print(len(s), *map(ord, s))"
# 9 116 97 99 95 108 111 103 105 110
```

### 5. Temporary bypass

**Write down the original values first.** Then temporarily replace the first
telnet method with `none` (`8`):

```bash
IDX="9.116.97.99.95.108.111.103.105.110"
LOGIN1="1.3.6.1.4.1.171.12.5.3.1.2.$IDX"
ENABLE1="1.3.6.1.4.1.171.12.5.4.1.2.$IDX"

snmpget -v2c -c private <SWITCH_IP> "$LOGIN1"
snmpget -v2c -c private <SWITCH_IP> "$ENABLE1"

snmpset -v2c -c private <SWITCH_IP> "$LOGIN1" i 8
snmpset -v2c -c private <SWITCH_IP> "$ENABLE1" i 8
```

Now telnet accepts any username/password, but the privilege level is `user#`.
Run `enable admin` with any password to get `admin#`.

### 6. Create a known account and save

```text
show account
create account admin <NEW_USER>
  Enter a case-sensitive new password: <NEW_PASS>
  Enter the new password again for confirmation: <NEW_PASS>
save
```

`save` is required — without it changes live only in RAM and are lost after
a reboot.

### 7. Restore authentication

If RADIUS is still unreachable, set `local`, not `radius` — otherwise login
breaks again:

```bash
snmpset -v2c -c private <SWITCH_IP> "$LOGIN1" i 6
snmpset -v2c -c private <SWITCH_IP> "$ENABLE1" i 4
```

Here `6` = local for login, `4` = the original RADIUS for `enable` (changing
it is optional: the new account already has Admin level). If RADIUS works,
restore both values to the original ones.

### 8. Verify and clean up

Open a **new** telnet session and verify login with the new credentials. Only
then remove the temporary account if you used one, and run `save` again.

```bash
python3 switch_telnet.py --host <SWITCH_IP> -u <NEW_USER> -p <NEW_PASS> 'show account'
```

---

## OID and value reference

| Item | OID | Purpose |
|---|---|---|
| `sysDescr` | `1.3.6.1.2.1.1.1.0` | model/firmware |
| `sysLocation` | `1.3.6.1.2.1.1.4.0` | RW-access check |
| Telnet method list | `1.3.6.1.4.1.171.12.5.7.1.2.0` | list used by telnet |
| Console method list | `1.3.6.1.4.1.171.12.5.7.1.1.0` | list used by console |
| Login method lists | `1.3.6.1.4.1.171.12.5.3.1.*` | login method table |
| Enable method lists | `1.3.6.1.4.1.171.12.5.4.1.*` | enable method table |
| Auth protocol | `1.3.6.1.4.1.171.12.5.1.1.0` | `3` = radius, `2` = local |

Table layout: `1.3.6.1.4.1.171.12.5.<3|4>.1.<column>.<index>`

| Column | Meaning |
|---|---|
| `1` | list name |
| `2` | `Method1Type` |
| `3` | `Method1ServerGroup` |
| `4` | `Method2Type` |
| `5` | `Method2ServerGroup` |

Observed `MethodType` values:

| Value | Method |
|---|---|
| `4` | `radius` |
| `6` | `local` |
| `8` | `none` (no authentication) |

> `none` is a transitional mode. Use it only while creating the account and
> restore the original value immediately. Never leave the device without
> authentication.

## Troubleshooting

- **Ping fails** — wrong IP/VLAN; find the address via LLDP or console boot
  output; try another port.
- **`snmpset` returns `notWritable (17)`** — the object is unsupported by this
  firmware. Do not try to bypass it; use the method list instead.
- **Login fails after `snmpset`** — you restored `radius` while the server is
  dead; set `6` (local).
- **`enable admin` asks for a password** — temporarily set `8` on the enable
  list, or create the account with Admin level directly.
- **3 failed attempts cause a 60-second pause** — this is normal protection;
  just wait.
- **No SNMP tools** — `sudo apt install snmp`.

## Repository files

- `switch_telnet.py` — telnet client with automatic login.
- `README.md` — Russian guide.
- `README.en.md` — English guide.
- `LICENSE` — MIT.

## License

MIT. See `LICENSE`.
