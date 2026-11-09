# A screen-reader check

The automated audit and the keyboard pass (wave 7) check names, structure, contrast, focus, and traps. What they cannot judge is how the app sounds: whether what a screen reader announces makes sense, at the moment it should. This is a short script for a person to do that, in about 40 minutes, on the demonstration organization. It is worth doing before each major release, and whenever a screen changes a lot.

## Set up

- **macOS:** VoiceOver (⌘F5) with Safari or Chrome. The VoiceOver keys are Control and Option together, written **VO** below; **VO-U** opens the rotor (headings, landmarks, links, form controls), **VO-Space** activates, and **Tab** moves between controls.
- **Windows:** NVDA with Firefox or Chrome. **Insert** is the NVDA key; **H** and **D** jump between headings and landmarks, **Tab** between controls, and **Insert-F7** lists them.
- The organization: `tessera demo demo` and `tessera --config demo/tessera.toml serve`, then open `http://127.0.0.1:8000/`. For task 1, turn accounts on and add a user first (*Adopting Tessera* shows how).

Listen with the screen off, or with your eyes closed, for a few minutes of each task: it is the surest way to notice what is missing.

## The tasks

For each, the **listen for** notes say what should be announced. Anything else is a finding.

1. **Sign in.** Tab through the form and sign in with a wrong password, then the right one.
   *Listen for:* each field's label; the error read out when it appears, without moving focus; the home page's heading after signing in.
2. **Find someone.** Open the command palette (⌘K, or Ctrl-K), type a name, choose a result.
   *Listen for:* a dialog with a search field; how many results there are as you type; each result's name and kind as you move through them; on choosing one, the new page's heading, with focus on it.
3. **Curate.** Open the review queue, move through a few suggestions, accept one with **a**, reject one with **r**, undo with **u**.
   *Listen for:* each suggestion's person, paper, and band as it comes up; the decision announced as it is made, and the undo; focus staying in the queue.
4. **Read a profile.** Open a person, move through the tabs, open a paper's evidence from its status mark, and close it.
   *Listen for:* the tabs as tabs, with the one selected; status marks read as words ("accepted", "suggested"), not symbols; the evidence as a dialog with a title; focus returning to the mark that opened it.
5. **Keep a profile.** On your own profile (or as a curator), add an entry to a section, leave a required field empty and save, then fill it in; leave the entry off the public page with the eye.
   *Listen for:* the editor as a dialog named for the section; the error on the field that needs it; the eye button saying what it will do, and its new state after.
6. **Edit a group's page.** In a group you manage, open **Edit page**, move a block up and down, and remove one.
   *Listen for:* each block's buttons naming the block ("Move People up"); each move and removal announced; focus staying in the list after a removal.
7. **Build a CV.** Start a new CV, untick a section, move another, and preview it.
   *Listen for:* each section's checkbox and its state; moves announced; the preview's headings in order.
8. **Read Insight.** Open Insight, move through the figures, and reach one figure's table.
   *Listen for:* each figure's title as a heading; the chart described by its title, not read as shapes; the table behind it with its column headers.

## Recording what you find

For each finding, note the page, what you did, what was announced, and what you expected, and open an issue labeled **accessibility** with those four things. A finding that blocks a task (something that cannot be reached or done) comes first; one that is only confusing next.

Results so far:

| Date | Who | Screen reader and browser | Findings |
|---|---|---|---|
| | | | |
