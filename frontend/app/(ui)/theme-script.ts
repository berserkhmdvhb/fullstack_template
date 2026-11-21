/**
 * The theme init script, in a plain module so the server layout imports the string itself.
 *
 * @packageDocumentation
 */

/**
 * Runs in `<head>` before first paint (see the root layout) so the `html` class is right with no flash.
 * It is the one place that decides the initial theme: the stored choice, else the system preference, else dark.
 *
 * @public
 */
export const THEME_INIT_SCRIPT =
  "try{var t=localStorage.getItem('theme');if(t!=='light'&&t!=='dark')t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';document.documentElement.classList.add(t)}catch(e){document.documentElement.classList.add('dark')}";
