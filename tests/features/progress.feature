Feature: What a long step says while it runs: little, on stderr

  Scenario: A quick step says only that it started
    Then a quick step says only that it started

  Scenario: A slow step counts every few seconds and says how long it took
    Then a slow step counts every few seconds and says how long it took

  Scenario: A step with nothing to do says nothing
    Then a step with nothing to do says nothing

  Scenario: Quiet says nothing
    Then quiet says nothing
