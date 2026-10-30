/**
 * file: src/components/ProfileMenu.tsx
 * importable: ProfileMenu
 * description: Picks a saved profile, saves the current values under a new name and deletes one.
 */

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import type { ProfileAnswer } from "../api/client";
import styles from "./ProfileMenu.module.css";

type Props = {
  profiles: ProfileAnswer[];
  currentId: number | null;
  currentName: string;
  onLoad: (profile: ProfileAnswer | null) => void;
  onDelete: (id: number) => void;
  onSaveAs: (name: string) => Promise<unknown>;
};

export function ProfileMenu({ profiles, currentId, currentName, onLoad, onDelete, onSaveAs }: Props) {
  const [open, setOpen] = useState(false);
  const [sure, setSure] = useState<number | null>(null);
  const [problem, setProblem] = useState("");
  const box = useRef<HTMLDivElement>(null);

  // Any click outside closes the menu.
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [open]);

  const pick = (profile: ProfileAnswer | null) => {
    onLoad(profile);
    setOpen(false);
  };

  // A name already taken leaves the menu open with the reason. Default is not a saved row, so
  // the backend would accept it and the menu would then show two of them.
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const name = String(new FormData(form).get("name")).trim();
    if (!name) return;
    if (name === "Default") return setProblem("A profile with this name already exists.");
    onSaveAs(name).then(
      () => {
        form.reset();
        setOpen(false);
      },
      (error: Error) => setProblem(error.message),
    );
  };

  return (
    <div className={styles.profile} ref={box}>
      <button type="button" className={styles.button} aria-haspopup="true" aria-expanded={open} onClick={() => {
        setProblem("");
        setOpen(!open);
      }}>
        <span>Profile</span>
        <b>{currentName}</b>
      </button>
      {open && (
        <div className={styles.menu}>
          <div className={styles.item} data-current={currentId === null ? "" : undefined}>
            <button type="button" onClick={() => pick(null)}>Default</button>
            <small>Built in</small>
          </div>
          {profiles.map((profile) => (
            <div key={profile.id} className={styles.item} data-current={profile.id === currentId ? "" : undefined}>
              <button type="button" onClick={() => pick(profile)}>{profile.name}</button>
              <button
                type="button"
                className={styles.remove}
                data-sure={sure === profile.id ? "" : undefined}
                onClick={() => {
                  if (sure === profile.id) onDelete(profile.id);
                  setSure(sure === profile.id ? null : profile.id);
                }}
              >
                {sure === profile.id ? "Sure?" : "Delete"}
              </button>
            </div>
          ))}
          <form onSubmit={submit}>
            <input name="name" maxLength={100} placeholder="Save these values as" aria-label="New profile name" autoComplete="off" />
            <button type="submit">Save</button>
          </form>
          {problem && <p className={styles.problem}>{problem}</p>}
        </div>
      )}
    </div>
  );
}
