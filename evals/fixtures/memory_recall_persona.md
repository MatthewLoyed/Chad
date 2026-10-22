# Fictional personal knowledge — memory recall eval fixture

Entirely invented. Never put real personal data in a tracked file.

## _core_profile

Alex Rivera. Lives in Portland, Oregon.
Engaged to Sam Okafor — wedding is April 11, 2028 at Cedar Hall in Bend, Oregon.
Graduated Northgate University in June 2027 with a BS in mechanical engineering.
Works as a design engineer at Harbor Dynamics.
Side project: Tidepool, a tide-forecasting app.

## _behavior

Present clarifying questions using the `ask_user_question` tool, not as a list in chat.
Prefer recent emails; do not treat year-old todos as current action items.

## identity

Alex Rivera is the user.
Email is alex.rivera@example.com.
Sam's email is sam.okafor@example.com.

## family

Father is named Marcus.
Mother is named Elena.
Brother is named Theo.
Fiancé is Sam Okafor (wedding April 11, 2028).
Sam's parents are Ruth Okafor and Daniel Okafor; Sam's sister is Iris.
They have a corgi named Biscuit.
Best friends and groomsmen: Priya Nandan (best woman), Owen Castellano, Marisol Vega.

## wedding

Wedding date is April 11, 2028 at Cedar Hall in Bend, Oregon.
Catering, florist, and the band are already booked; photographer is still open.

## career

Design engineer at Harbor Dynamics in Portland, working on tidal turbine housings.
Reports to Priya Nandan. Nadia Kwon is on the same team.

## school

Graduated Northgate University June 2027 with a BS in mechanical engineering.
Tuition was covered by the Grieve Foundation scholarship.

## projects

Tidepool — a tide-forecasting app; Alex is the sole developer.
Harbor Dynamics internal tooling is a side responsibility.

## interests

Likes bouldering and sea kayaking.
Favorite foods are green curry and sourdough.
Watches documentaries, mostly nature and history.

## hardware

Desktop CPU is an Intel Core i7-13700K with 64GB of RAM.
GPU is an NVIDIA RTX 4070.

## routine

Weekday pattern: wake around 7am, at Harbor Dynamics until 4pm, then bouldering or Tidepool work.

## _graph

- Person:Alex
- Person:Sam
- Person:Marcus
- Person:Elena
- Person:Theo
- Person:Ruth
- Person:Daniel
- Person:Iris
- Person:Priya
- Person:Owen
- Person:Marisol
- Person:Nadia
- Pet:Biscuit
- Place:Portland
- Place:Bend
- Place:CedarHall
- Org:Northgate (in Corvallis, Oregon)
- Org:HarborDynamics
- Project:Tidepool
- Project:Kelpline (a marine sensor logger, weekends only)
- Document:VenueContract (signed, in the wedding folder)
- Event:Wedding
- Sam -partner_of-> Alex
- Marcus -parent_of-> Alex
- Elena -parent_of-> Alex
- Theo -sibling_of-> Alex
- Ruth -parent_of-> Sam
- Daniel -parent_of-> Sam
- Iris -sibling_of-> Sam
- Priya -best_man_of-> Alex
- Priya -friend_of-> Alex
- Owen -groomsman_of-> Alex
- Marisol -groomsman_of-> Alex
- Nadia -works_with-> Alex
- Alex -lives_in-> Portland
- Alex -employed_by-> HarborDynamics
- Alex -employed_by-> Org:Tidewater (since 2024-01, until 2026-02)
- Alex -attended-> Northgate
- Alex -owns-> Tidepool
- Alex -owns-> Kelpline
- Document:VenueContract -about-> Event:Wedding
- Alex -owns-> Biscuit
- Wedding -happened_at-> CedarHall
- CedarHall -located_at-> Bend
