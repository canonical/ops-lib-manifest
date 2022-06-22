# ops-lib-manifests

## Rationale for this library
Most kubernetes projects deploy with manifest files which promote suggested deployment
parameters, but those manifests aren't consistent about which options are requirements
and which options are variable. In some cases the project's distribution uses different
means of indicating a need for replacement.

For example, the following reference from [vsphere-cloud-controller-manager](https://github.com/kubernetes/cloud-provider-vsphere/blob/master/releases/v1.23/vsphere-cloud-controller-manager.yaml#L11-L24)
gives the consumer of this yaml an indication there should be usernames and passwords set
in the Secret object.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: vsphere-cloud-secret
  labels:
    vsphere-cpi-infra: secret
    component: cloud-controller-manager
  namespace: kube-system
  # NOTE: this is just an example configuration, update with real values based on your environment
stringData:
  10.0.0.1.username: "<ENTER_YOUR_VCENTER_USERNAME>"
  10.0.0.1.password: "<ENTER_YOUR_VCENTER_PASSWORD>"
  1.2.3.4.username: "<ENTER_YOUR_VCENTER_USERNAME>"
  1.2.3.4.password: "<ENTER_YOUR_VCENTER_PASSWORD>"
```

Automation tools like a juju charm will need to read these yaml manifest files, manipulate
its content, and deploy those manifests when any of the **configurable** data is changed.


## Supporting Multiple Releases
Likewise, the projects which release reference manifest files, will also release versions
of manifests. It's possible for a charm to load all the supported manifest files into a 
folder structure such the charm supports multiple releases. This library supports this 
requirements by having the charm store upstream manifest files unchanged in a folder 
structure like this:

```
<base_path>
├── version                  - a file containing the default version
├── manifests                - a folder containing all the releases
│   ├── v1.1.10              - a folder matching a configurable version
│   │   ├── manifest-1.yaml  - any file with a `.yaml` file type
│   │   └── manifest-2.yaml
│   ├── v1.1.11
│   │   ├── manifest-1.yaml
│   │   └── manifest-2.yaml
│   │   └── manifest-3.yaml
```

Key file-heirarchy requirements
-------------------------------
|  |  |
| --- | --- |
| **$base_path** | A single charm can support multiple manifest releases
| **version**    | A text file indicating to the library which manifest version is the default when the 'release' config is unspecified |
| **manifests**  | A folder containing the individual release manifest folders |
| **$release**   | A folder containing the yaml files of the specific release |

## Sample Usage

Once your charm includes the above manifest file heirachy, your charm will need to define the
mutations the library should make to the manifests. 


```python
from ops.manifests import Collector, Manifests, ManifestLabel, ConfigRegistry

class ExampleManifests(Manifests):
    def __init__(self, charm_config):
        manipulations = [
            ManifestLabel(self),
            ConfigRegistry(self),
            UpdateSecret(self),
        ]
        super().__init__("example", "upstream/example", manipulations=manipulations)
        self.charm_config = charm_config

    @property
    def config(self) -> Dict:
        """Returns config mapped from charm config and joined relations."""
        config = dict(**self.charm_config)

        for key, value in dict(**config).items():
            if value == "" or value is None:
                del config[key]  # blank out keys not currently set to something

        config["release"] = config.pop("example-release", None)
        return config


class ExampleCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)

        # collection of ManifestImpls
        self.manifests = [ ExampleManifests(self.charm.config) ]

        # create an Actions handler object
        self.collector = Collector(*self.manifests)

        # Register actions callbacks
        self.framework.observe(self.on.list_versions_action, self.collector.list_versions)
        
        # Register updstate status callbacks
        self.framework.observe(self.on.update_status, self._update_status)
    
    def _update_status(self, _):
        unready = self.collector.unready
        if unready:
            self.unit.status = WaitingStatus(", ".join(unready))
        else:
            self.unit.status = ActiveStatus("Ready")
            self.unit.set_workload_version(self.collector.short_version)
            self.app.status = ActiveStatus(self.collector.long_version)
        
```

## Manipulations

### Patching a manifest resource
Some resources already exist within the manifest, and just need to be updated.

#### Built in Patchers
* `ManifestLabel` - add to each resources `metadata.label` the label with `<self.manifest.name>` = `true`
* `ConfigRegistry` - updates the image registry of every `Pod`, `DaemonSet`, `Deployment`, and `StatefulSet` from the `image-registry` config item in the config properties `Dict`. If the charm doesn't wish to alter
the config, ensure nothing exists in the `image-registry`.
* `update_toleration` - not officially a patcher, but can be used by a custom Patcher to adjust tolerations on `Pod`, `DaemonSet`, `Deployment`, and `StatefulSet` resources.

### Adding a manifest resource
Some resources do not exist in the release manifest and must be added. The `Addition` manipulations are added
before the rest of the `Patch` manipulations are applied.

#### Built in Adders
* `CreateNamespace` - Creates a namespace resource using either the manifest's default namespace or 
                    an argument passed in to the constructor of this class. 

### Custom Patchers/Additions
Of course the built-ins will not be enough, so your charm may extend its own manipulations by defining
new objects which inherit from either `Patch` or `Addition`. 