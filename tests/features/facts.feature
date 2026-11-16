Feature: The facts registry: its shape, and that the plan's prose agrees with it
  On a made-up registry (fixtures/root/). The repository holding a real
  registry also tests its own data. The last three drive a real pub and
  pub-store (PUB=..., or both on PATH) and are skipped without one.

  Scenario: Every local id the registry names is defined
    Then every local id the registry names is defined

  Scenario: A singular is located by coordinates never by a typed cell
    Then a singular is located by coordinates never by a typed cell

  Scenario: Each lint allowance still matches a line
    Then each lint allowance still matches a line

  Scenario: A disagreeing area beside its room is reported
    Then a disagreeing area beside its room is reported

  Scenario: An agreeing figure and an allowed quote pass
    Then an agreeing figure and an allowed quote pass

  Scenario: A registry nothing was gathered from composes all of it
    Then a registry nothing was gathered from composes all of it

  Scenario: An unchanged registry composes nothing
    Then an unchanged registry composes nothing

  Scenario: An edited value revises that fact alone
    Then an edited value revises that fact alone

  Scenario: A revised definition revises what cites it
    Then a revised definition revises what cites it

  Scenario: Inputs do not depend on the pub that composes them
    Then inputs do not depend on the pub that composes them

  Scenario: Check reports a registry edit nobody gathered
    Then check reports a registry edit nobody gathered

  Scenario: Check reports a fact removed without retraction
    Then check reports a fact removed without retraction

  Scenario: Check reports a stray object and tampered bytes
    Then check reports a stray object and tampered bytes

  Scenario: An objects address is the sha256 its name holds
    Then an objects address is the sha256 its name holds

  Scenario: A record that is not an objects name reaches nothing
    Then a record that is not an objects name reaches nothing

  Scenario: Fetch takes what is missing with a token and checks each
    Then fetch takes what is missing with a token and checks each

  Scenario: Fetch says what it is fetching and nothing when all is here
    Then fetch says what it is fetching and nothing when all is here

  Scenario: Fetch refuses bytes that do not hash to the name
    Then fetch refuses bytes that do not hash to the name

  Scenario: Upload sends each object and refuses another address
    Then upload sends each object and refuses another address

  Scenario: Check without a local copy compares only the registry and the record
    Then check without a local copy compares only the registry and the record

  Scenario: Gathering an unchanged registry again adds nothing
    Then gathering an unchanged registry again adds nothing

  Scenario: An edited value is a new version and the old object stays
    Then an edited value is a new version and the old object stays

  Scenario: A removed open note is retracted not deleted
    Then a removed open note is retracted not deleted
