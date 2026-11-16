Feature: The suggestion job (tools/suggest/): grouping, drafting into the review service's shape, and signing triage annotations
  Claude is never called: a fake client answers. The signing test needs a
  pub built from publet-cli v0.2.2 or later (`make pub`, or PUB=...) and is
  skipped without one.

  Scenario: Canonical cbor is what pub writes
    Then canonical cbor is what pub writes

  Scenario: The snapshot holds every gathered extract by kind
    Then the snapshot holds every gathered extract by kind

  Scenario: A draft becomes suggestions in the review services shape
    Then a draft becomes suggestions in the review services shape

  Scenario: Rejected suggestions free their extracts
    Then rejected suggestions free their extracts

  Scenario: A run writes suggestions and is not repeated
    Then a run writes suggestions and is not repeated

  Scenario: A refused group is skipped and the run not recorded
    Then a refused group is skipped and the run not recorded

  Scenario: Triage is signed by the suggester key
    Then triage is signed by the suggester key

  Scenario: A claude code session can draft in place of the api
    Then a claude code session can draft in place of the api
