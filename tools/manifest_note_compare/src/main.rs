//! **Whether a newly built package asks for no more than the served one**: the manifest note check
//! of the recipe bump (helpers/recipe_bump.py, record §328).
//!
//! `manifest_note_compare <served.nifepkg> <built.nifepkg>` reads every program of the built
//! package, finds the program of the same name in the served one, and compares the notes each
//! carries, read and decoded by nife's own crates. It prints one line per program and exits 0 when
//! every program's notes are the same or grant a subset, 1 when any program is held, and 2 when a
//! package cannot be read at all.
//!
//! The rule is conservative: what it cannot show to be narrower holds. A manifest note is the same
//! when its bytes are; otherwise both are decoded, and the built one is narrower only when every
//! yes-or-no grant it asks for the served one asked for too, its index store access is no wider,
//! its zones are among the served zones (or the served one asked for the root zone, any name), it
//! asks to run as a service only if the served one did, and every other field is equal. The
//! subtree grant note and the verb table must be byte-identical, absent from both counting as
//! identical. A program the served package does not carry, a note either side cannot decode, and a
//! note on one side only all hold.
//!
//! Name provisional: the program and its output.

use capability_manifest_note::{MANIFEST, OWNER, SUBTREE_GRANTS, VERBS};
use grant_contract::{IndexStoreDeclaration, Manifest};
use std::process::ExitCode;

/// What one program's notes come to.
#[derive(Debug, PartialEq, Eq)]
enum Verdict {
    /// Every note is byte-identical.
    Same,
    /// The built program asks for less; the strings name what it no longer asks for.
    Narrower(Vec<String>),
    /// The built program may ask for more, or a note could not be compared; the strings say why.
    Held(Vec<String>),
}

/// A program's three notes, as `static_elf` finds them, or why they could not be read.
struct Notes<'a> {
    manifest: Option<&'a [u8]>,
    subtree_grants: Option<&'a [u8]>,
    verbs: Option<&'a [u8]>,
}

fn notes(elf: &[u8]) -> Result<Notes<'_>, String> {
    // A package is built for one of nife's machines, rarely the host's, so the machine check
    // accepts any of them; whichever the program names, its notes are read the same way.
    let elf = [
        static_elf::MACHINE_AARCH64,
        static_elf::MACHINE_RISCV64,
        static_elf::MACHINE_X86_64,
    ]
    .into_iter()
    .find_map(|machine| static_elf::Elf::parse_for_machine(elf, machine).ok())
    .ok_or_else(|| "not an ELF nife reads for any of its machines".to_string())?;
    let read = |kind| {
        elf.note(OWNER, kind)
            .map_err(|e| format!("its note of type {kind} cannot be read: {e:?}"))
    };
    Ok(Notes {
        manifest: read(MANIFEST)?,
        subtree_grants: read(SUBTREE_GRANTS)?,
        verbs: read(VERBS)?,
    })
}

/// The programs of a package, by name, with their bytes.
fn programs(bytes: &[u8]) -> Result<Vec<(String, &[u8])>, String> {
    let package = package_archive::Package::parse(bytes)
        .map_err(|e| format!("not a package nife reads: {e:?}"))?;
    let mut out = Vec::new();
    for index in 0..package.len() {
        if let (Some(member), Some(name)) = (
            package.member(index),
            package_archive::program_name(&package, index),
        ) {
            out.push((name.to_string(), member));
        }
    }
    Ok(out)
}

/// The served and built programs' notes, compared.
fn compare_notes(served: &Notes<'_>, built: &Notes<'_>) -> Verdict {
    let mut held = Vec::new();
    let mut narrower = Vec::new();
    if served.subtree_grants != built.subtree_grants {
        held.push("its subtree grant note changed".to_string());
    }
    if served.verbs != built.verbs {
        held.push("its verb table changed".to_string());
    }
    match (served.manifest, built.manifest) {
        (None, None) => {}
        (Some(a), Some(b)) if a == b => {}
        (Some(a), Some(b)) => match compare_manifests(a, b) {
            Verdict::Same => {}
            Verdict::Narrower(n) => narrower = n,
            Verdict::Held(h) => held.extend(h),
        },
        (None, Some(_)) => held.push("it carries a manifest note the served one does not".into()),
        (Some(_), None) => held.push("it carries no manifest note, and the served one does".into()),
    }
    if !held.is_empty() {
        Verdict::Held(held)
    } else if narrower.is_empty() {
        Verdict::Same
    } else {
        Verdict::Narrower(narrower)
    }
}

/// How wide an index store grant is, for comparing two.
fn index_access(i: IndexStoreDeclaration) -> u8 {
    match i {
        IndexStoreDeclaration::Forbidden => 0,
        IndexStoreDeclaration::ReadOnly => 1,
        IndexStoreDeclaration::ReadWrite => 2,
    }
}

/// Two manifest descriptors whose bytes differ, decoded with nife's decoder and compared.
fn compare_manifests(served: &[u8], built: &[u8]) -> Verdict {
    let decoded = |d| {
        Ok::<_, capability_manifest_note::Error>((
            capability_manifest_note::decode(d)?,
            capability_manifest_note::zones(d)?,
            capability_manifest_note::asks_service(d)?,
        ))
    };
    let (old, old_zones, old_service) = match decoded(served) {
        Ok(v) => v,
        Err(e) => return Verdict::Held(vec![format!("the served manifest note: {e}")]),
    };
    let (new, new_zones, new_service) = match decoded(built) {
        Ok(v) => v,
        Err(e) => return Verdict::Held(vec![format!("the built manifest note: {e}")]),
    };
    let mut held = Vec::new();
    let mut narrower = Vec::new();
    let mut flag = |name: &str, old: bool, new: bool| {
        if new && !old {
            held.push(format!(
                "it asks for `{name}`, which the served one does not"
            ));
        } else if old && !new {
            narrower.push(format!("it no longer asks for `{name}`"));
        }
    };
    // Every field by name, with no `..`: a field nife adds to the manifest fails to compile here
    // rather than passing unread.
    let Manifest {
        program_argument,
        memory_grant,
        file,
        directory,
        short_options,
        output,
        input,
        reports,
        interruptible,
        clock,
        domain,
        configuration,
        entropy,
        network,
        machine,
        share,
        reboot,
        sync,
        installer,
        catalog,
        indexes,
        activation_directory,
        console_input,
        startup,
    } = new;
    flag("reports", old.reports, reports);
    flag("interruptible", old.interruptible, interruptible);
    flag("clock", old.clock, clock);
    flag("domain", old.domain, domain);
    flag("configuration", old.configuration, configuration);
    flag("entropy", old.entropy, entropy);
    flag("network", old.network, network);
    flag("machine", old.machine, machine);
    flag("share", old.share, share);
    flag("reboot", old.reboot, reboot);
    flag("sync", old.sync, sync);
    flag("installer", old.installer, installer);
    flag("catalog", old.catalog, catalog);
    flag(
        "activation_directory",
        old.activation_directory,
        activation_directory,
    );
    flag("console_input", old.console_input, console_input);
    flag("service", old_service, new_service);
    let mut equal = |name: &str, same: bool| {
        if !same {
            held.push(format!(
                "its `{name}` changed, and the check reads no order in it"
            ));
        }
    };
    equal("program_argument", program_argument == old.program_argument);
    equal("memory_grant", memory_grant == old.memory_grant);
    equal("file", file == old.file);
    equal("directory", directory == old.directory);
    equal("short_options", short_options == old.short_options);
    equal("output", output == old.output);
    equal("input", input == old.input);
    equal("startup", startup == old.startup);
    match index_access(indexes).cmp(&index_access(old.indexes)) {
        core::cmp::Ordering::Greater => held.push("its `indexes` access is wider".into()),
        core::cmp::Ordering::Less => narrower.push("its `indexes` access is narrower".into()),
        core::cmp::Ordering::Equal => {}
    }
    for zone in &new_zones {
        if !old_zones.has_root() && !old_zones.iter().any(|z| z == zone) {
            held.push(format!(
                "it asks for the zone `{}`, which the served one does not",
                String::from_utf8_lossy(zone)
            ));
        }
    }
    for zone in &old_zones {
        if !new_zones.iter().any(|z| z == zone) {
            narrower.push(format!(
                "it no longer asks for the zone `{}`",
                String::from_utf8_lossy(zone)
            ));
        }
    }
    if !held.is_empty() {
        Verdict::Held(held)
    } else if narrower.is_empty() {
        // Different bytes that decode to the same grants: nothing asked for moved.
        Verdict::Same
    } else {
        Verdict::Narrower(narrower)
    }
}

/// Every built program against the served program of its name.
fn compare_packages(served: &[u8], built: &[u8]) -> Result<Vec<(String, Verdict)>, String> {
    let served = programs(served).map_err(|e| format!("the served package: {e}"))?;
    let built = programs(built).map_err(|e| format!("the built package: {e}"))?;
    let mut out = Vec::new();
    for (name, bytes) in &built {
        let verdict = match served.iter().find(|(n, _)| n == name) {
            None => Verdict::Held(vec![
                "the served package carries no program of this name".into(),
            ]),
            Some((_, old)) => match (notes(old), notes(bytes)) {
                (Ok(a), Ok(b)) => compare_notes(&a, &b),
                (Err(e), _) => Verdict::Held(vec![format!("the served program: {e}")]),
                (_, Err(e)) => Verdict::Held(vec![format!("the built program: {e}")]),
            },
        };
        out.push((name.clone(), verdict));
    }
    if out.is_empty() {
        return Err("the built package carries no program".into());
    }
    Ok(out)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [served, built] = args.as_slice() else {
        eprintln!("usage: manifest_note_compare <served.nifepkg> <built.nifepkg>");
        return ExitCode::from(2);
    };
    let read = |p: &String| std::fs::read(p).map_err(|e| format!("{p}: {e}"));
    let result = read(served).and_then(|s| read(built).and_then(|b| compare_packages(&s, &b)));
    let verdicts = match result {
        Ok(v) => v,
        Err(e) => {
            eprintln!("manifest_note_compare: {e}");
            return ExitCode::from(2);
        }
    };
    let mut any_held = false;
    for (name, verdict) in verdicts {
        match verdict {
            Verdict::Same => println!("{name}: same"),
            Verdict::Narrower(why) => println!("{name}: narrower: {}", why.join("; ")),
            Verdict::Held(why) => {
                any_held = true;
                println!("{name}: held: {}", why.join("; "));
            }
        }
    }
    if any_held {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use capability_manifest_note::{encode, encode_zoned, zoned_descriptor_bytes};
    use grant_contract::*;

    /// A program that asks for its output, its exit report, the clock and entropy: the served
    /// fixture. No index store, which no note may carry.
    const SERVED: Manifest = Manifest {
        program_argument: ProgramArgumentDeclaration::Forbidden,
        memory_grant: MemoryGrantDeclaration::Forbidden,
        file: FileGrantDeclaration::Forbidden,
        directory: DirectoryGrantDeclaration::Forbidden,
        short_options: NO_SHORT_OPTIONS,
        output: OutputDeclaration::Bytes,
        input: InputDeclaration::Forbidden,
        reports: true,
        interruptible: false,
        clock: true,
        domain: false,
        configuration: false,
        entropy: true,
        network: false,
        machine: false,
        share: false,
        reboot: false,
        sync: false,
        installer: false,
        catalog: false,
        indexes: IndexStoreDeclaration::Forbidden,
        activation_directory: false,
        console_input: false,
        startup: StartupContract::Native,
    };

    fn bare(m: &Manifest) -> Notes<'static> {
        Notes {
            manifest: Some(Vec::leak(encode(m).to_vec())),
            subtree_grants: None,
            verbs: None,
        }
    }

    #[test]
    fn an_unchanged_note_is_the_same() {
        assert_eq!(compare_notes(&bare(&SERVED), &bare(&SERVED)), Verdict::Same);
    }

    #[test]
    fn a_narrowed_note_passes() {
        let built = Manifest {
            clock: false,
            entropy: false,
            ..SERVED
        };
        let Verdict::Narrower(why) = compare_notes(&bare(&SERVED), &bare(&built)) else {
            panic!("a narrower manifest must pass as narrower");
        };
        assert_eq!(why.len(), 2, "{why:?}");
    }

    #[test]
    fn a_widened_note_holds() {
        let built = Manifest {
            network: true,
            clock: false,
            ..SERVED
        };
        let Verdict::Held(why) = compare_notes(&bare(&SERVED), &bare(&built)) else {
            panic!("a wider manifest must hold");
        };
        assert!(why.iter().any(|w| w.contains("`network`")), "{why:?}");
    }

    #[test]
    fn a_changed_field_with_no_order_holds() {
        let built = Manifest {
            output: OutputDeclaration::Words,
            ..SERVED
        };
        assert!(matches!(
            compare_notes(&bare(&SERVED), &bare(&built)),
            Verdict::Held(_)
        ));
    }

    const ONE_ZONE: &[&str] = &["example.net"];
    const TWO_ZONES: &[&str] = &["example.net", "example.org"];

    fn zoned<const N: usize>(zones: &[&str]) -> Notes<'static> {
        let d: [u8; N] = encode_zoned(
            &Manifest {
                network: true,
                ..SERVED
            },
            zones,
        );
        Notes {
            manifest: Some(Vec::leak(d.to_vec())),
            subtree_grants: None,
            verbs: None,
        }
    }

    #[test]
    fn a_new_zone_holds_and_a_dropped_one_passes() {
        let one = zoned::<{ zoned_descriptor_bytes(ONE_ZONE) }>(ONE_ZONE);
        let two = zoned::<{ zoned_descriptor_bytes(TWO_ZONES) }>(TWO_ZONES);
        assert!(matches!(compare_notes(&one, &two), Verdict::Held(_)));
        assert!(matches!(compare_notes(&two, &one), Verdict::Narrower(_)));
    }

    #[test]
    fn an_undecodable_note_holds() {
        let mut broken = encode(&SERVED).to_vec();
        broken[0] = 9;
        let built = Notes {
            manifest: Some(&broken),
            subtree_grants: None,
            verbs: None,
        };
        assert!(matches!(
            compare_notes(&bare(&SERVED), &built),
            Verdict::Held(_)
        ));
    }

    #[test]
    fn a_note_on_one_side_only_holds() {
        let none = Notes {
            manifest: None,
            subtree_grants: None,
            verbs: None,
        };
        assert!(matches!(
            compare_notes(&none, &bare(&SERVED)),
            Verdict::Held(_)
        ));
        assert!(matches!(
            compare_notes(&bare(&SERVED), &none),
            Verdict::Held(_)
        ));
        let mut grants = bare(&SERVED);
        grants.subtree_grants = Some(&[1, 0, 0, 0, 1, 0, 0, 0]);
        assert!(matches!(
            compare_notes(&bare(&SERVED), &grants),
            Verdict::Held(_)
        ));
    }
}
