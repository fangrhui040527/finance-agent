/* Route -> screen module. Static imports so one missing file is a load error,
 * not a blank page discovered later. */

import { main } from "/screens/main.js";
import { why } from "/screens/why.js";
import { prices } from "/screens/prices.js";
import { sizing } from "/screens/sizing.js";
import { portfolio } from "/screens/portfolio.js";
import { thesis } from "/screens/thesis.js";
import { predictions } from "/screens/predictions.js";
import { trace } from "/screens/trace.js";
import { learn } from "/screens/learn.js";
import { world } from "/screens/world.js";
import { agents } from "/screens/agents.js";
import { settings } from "/screens/settings.js";

export const screens = {
  main,
  why,
  prices,
  sizing,
  portfolio,
  thesis,
  predictions,
  trace,
  learn,
  world,
  agents,
  settings,
};
