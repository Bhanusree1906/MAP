# Salesforce CLI reference

Default target org is already authenticated. Add `--target-org <alias>` only when you must override it.

## Org

```bash
sf org display
sf org list
sf data query --query "SELECT Id, Name FROM Account LIMIT 5"
```

## Retrieve / deploy (source format)

```bash
sf project retrieve start --source-dir force-app/main/default/classes
sf project retrieve start --metadata ApexClass:MyClass,LightningComponentBundle:myComponent
sf project deploy start --source-dir force-app/main/default/classes/MyClass.cls --wait 10
sf project deploy start --manifest manifest/package.xml --wait 10
sf project deploy start --dry-run --source-dir force-app --wait 10
```

Preview a deploy without saving: `--dry-run`. Do not use `--ignore-conflicts` unless you have retrieved first and still have a documented conflict.

## Apex tests

```bash
sf apex run test --tests MyClassTest --result-format human --wait 10 --code-coverage
sf apex run test --test-level RunLocalTests --wait 20 --result-format human
sf apex get test --test-run-id <id> --code-coverage
```

`--test-level RunLocalTests` is for a full local suite. Prefer `--tests` listing the classes you touched.

## Apex anonymous / logs

```bash
sf apex run --file scripts/apex/scratch.apex
sf apex tail log --follow
```

## Metadata type folders (source format)

| What | Path under `force-app/main/default/` |
|---|---|
| Apex class | `classes/MyClass.cls` + `MyClass.cls-meta.xml` |
| Apex trigger | `triggers/MyTrigger.trigger` + meta |
| LWC | `lwc/myComponent/` |
| Aura | `aura/myComponent/` |
| Custom object | `objects/MyObject__c/MyObject__c.object-xml` |
| Custom field | `objects/Account/fields/MyField__c.field-xml` |
| Permission set | `permissionsets/My_Perm.permissionset-xml` |
| Flexi page | `flexipages/MyPage.flexipage-xml` |
| Flow | `flows/My_Flow.flow-xml` |
| Custom label | `labels/CustomLabels.labels-xml` |
| Static resource | `staticresources/` |
| Layout | `layouts/Account-Account Layout.layout-xml` |

Exact XML suffixes follow what this repo already uses (`-meta.xml` vs `.object-xml`). Copy a sibling file; do not invent a new suffix.

## Apex class meta.xml skeleton

Keep `apiVersion` equal to `sourceApiVersion` in `sfdx-project.json`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">
    <apiVersion>62.0</apiVersion>
    <status>Active</status>
</ApexClass>
```

## LWC js-meta.xml skeleton

```xml
<?xml version="1.0" encoding="UTF-8"?>
<LightningComponentBundle xmlns="http://soap.sforce.com/2006/04/metadata">
    <apiVersion>62.0</apiVersion>
    <isExposed>true</isExposed>
    <targets>
        <target>lightning__RecordPage</target>
        <target>lightning__AppPage</target>
        <target>lightning__HomePage</target>
    </targets>
</LightningComponentBundle>
```

## Common failures

- **Invalid Session / not logged in**: do not browser-login. Report that org auth is missing; the pipeline injects the SFDX auth URL at sandbox start.
- **Insufficient access / Cannot modify metadata**: the connected user lacks Modify All Data / the relevant permission set. Stop and report.
- **Test failures on deploy**: fix tests or the production code; do not deploy with `--test-level NoTestRun` unless the ticket explicitly allows it (scratch/dev only).
- **Duplicate value / missing required field on custom metadata**: retrieve the org version, then merge.
