Feature: Paper extracts: the registry's shape, what is gathered, and the check page
  On a made-up registry (fixtures/root/). The repository holding a real
  registry also tests its own data.

  Scenario: The registry is well formed
    Then the registry is well formed

  Scenario: A quote over 25 words is refused
    Then a quote over 25 words is refused

  Scenario: A locator starts with a page of the file
    Then a locator starts with a page of the file

  Scenario: The value must be in its quote
    Then the value must be in its quote

  Scenario: A unit outside its kind is refused
    Then a unit outside its kind is refused

  Scenario: A quote is found across line breaks superscripts and an ellipsis
    Then a quote is found across line breaks superscripts and an ellipsis

  Scenario: Status follows the spec
    Then status follows the spec

  Scenario: Nothing is gathered until a person checks it
    Then nothing is gathered until a person checks it

  Scenario: A checked extract brings what it cites
    Then a checked extract brings what it cites

  Scenario: A check claim waits for every extract it compares
    Then a check claim waits for every extract it compares

  Scenario: A rejected extract is not gathered
    Then a rejected extract is not gathered

  Scenario: Check reports a checked extract nobody gathered
    Then check reports a checked extract nobody gathered

  Scenario: Check reports an object not uploaded
    Then check reports an object not uploaded

  Scenario: Only an open licence pdf has an address
    Then only an open licence pdf has an address

  Scenario: The check page records a finding and refuses a bad edit
    Then the check page records a finding and refuses a bad edit

  Scenario: An edited unit moves the extract to the kind that lists it
    Then an edited unit moves the extract to the kind that lists it

  Scenario: The check page lists every extract with its page
    Then the check page lists every extract with its page

  Scenario: Gathering signs checked extracts once
    Then gathering signs checked extracts once

  Scenario: Fetch names itself because cloudflare refuses pythons default
    Then fetch names itself because cloudflare refuses pythons default

  Scenario: The page records a finding from itself
    Then the page records a finding from itself

  Scenario: A finding from anywhere else changes nothing
    Then a finding from anywhere else changes nothing

  Scenario: Another host reads nothing
    Then another host reads nothing

  Scenario: A pdf is served by its sha and a miss names no folder
    Then a pdf is served by its sha and a miss names no folder

  Scenario: Upload refuses a sha that is not one before reading anything
    Then upload refuses a sha that is not one before reading anything
