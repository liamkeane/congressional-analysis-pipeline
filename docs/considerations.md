# Considerations to keep in mind

1. Nested, inconsistent JSON structures. The API returns deeply nested objects (actions have optional committee, sourceSystem, calendarNumber sub-objects that aren't always present). Flattening this into clean relational tables — deciding what becomes a column vs. a separate table — can be tricky.

2. Bill numbering resets every Congress. HR 1 in the 118th Congress is a completely different bill from HR 1 in the 119th.
    a. Limiting the scope to one Congress session would handle this, but I'd like to maintain the flexibility to backfill other sessions.
    b. Organizing raw data into info/datetime/congress_number will handle this.

3. Action type isn't a clean state machine in practice. A bill can go back to committee after floor action, get re-referred, or sit in "ResolvingDifferences" for months while also having new committee actions logged. Deriving a single current status field means writing real logic (e.g., "most recent action of certain types wins" with tie-breaking rules), not just taking MAX(action_date).
    a. Handle with dbt.

4. Cosponsors are not append-only. Cosponsors can withdraw, and the API tracks both current count and total-including-withdrawn separately.

5. Missing/sparse data by era. Subject terms are inconsistent pre-1973 through 2008 (Legislative Indexing Vocabulary vs. later CRS terms), and pre-1995 bills have thinner sponsor/cosponsor data.
    a. Keep scope to recent Congresses for now.

6. Text fields need real cleaning. Action text fields are free-form legislative language ("Passed Senate without amendment by Yea-Nay Vote. 79 - 19. Record Vote Number: 71."). If you want to extract structured signal from this (e.g., pull out vote counts or record vote numbers), that's a legitimate parsing/regex task — good for showing SQL string manipulation or a light dbt macro.

7. Many-to-many relationships everywhere. Bills-to-subjects, bills-to-committees, bills-to-cosponsors — all many-to-many, which is exactly why the bridge-table pattern I mentioned earlier matters. Handling these correctly (vs. flattening into comma-delimited strings, which is a common junior mistake) is a good signal.

8. Duplicate/near-duplicate legislation. Companion bills (same bill introduced in both chambers) and related bills aren't automatically merged — the API has a relatedBills field, but reconciling "is this really the same policy effort" is a judgment call, not a clean join. You could choose to model this or explicitly scope it out — either is a fine interview answer as long as you can explain the decision.

## Roadblocks

1. My initial schema design was a bit ambitious. I had to simplify it to make my MVP more attainable. I'll leave a more complicated downstream schema as potential future work.
    a. dim_bill and dim_member: dropped valid_from, valid_to and is_current. Both are Type 1 for now. Plans to introduce later.

2. Does a backfill run that crosses midnight, or resumes the next day, write into one dt= folder or two? I'll have to pick one, handle it, and justify why.

3. Since the API is public and has request limitations, an hours (or day) long backfill is bound to fail for one reason or another-- and debugging will be slow. Also, the data may change while the backfill is being run...
    a. Loop through the list endpoint for bills *before* making sub requests for their details. With the limit of 250 per page and 20k bills, that comes to about 160 requests in the worst case, which should only take a couple of minutes to request. 
    b. Backfilling bill details should be resumable since it would be around 40k(!) requests and will take hours. So it'll be a bummer when that inevitably fails. But by saving the list first, we can skip any bill whose files already exist in the landing zone, so we avoid those redundant requests (assuming that the call takes place same day).
    c. Note that the members data is not (and should not) be included in bill detail loop. We can get all the info we need from the members list endpoint, which will only take a couple minutes and avoid redundancy.