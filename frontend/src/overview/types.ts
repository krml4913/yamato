import type { Overview } from "../api/client";

export type Ship = Overview["ships"][number];
export type ShipState = NonNullable<Ship["state"]>;
export type Seat = ShipState["seats"][number];
export type Wait = Overview["waits"][number];
